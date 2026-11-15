"""SSE del episodio de alerta: una alerta NUEVA llega como `alerta` (ronda 2b).

El tablero (FE `sseClient.ts`) avisa de una alerta nueva solo con el evento
`alerta`; `episodio_actualizado` refresca la fila de una alerta que ya conoce.
Antes, ambos caminos de ingesta (MQTT en `interface/main.py` y HTTP en
`api/lecturas_router.py`) decidían el tipo solo con «¿había un episodio
abierto antes de la lectura?». Cuando la lectura cerraba ese episodio y abría
otro, la alerta nueva llegaba como `episodio_actualizado`, sin aviso:

- IA-D18: el episodio crítico ATENDIDO sin continuidad se cierra y la
  excursión abre una alerta PENDIENTE nueva;
- escalamiento preventiva → crítica (y el cambio de nivel inverso, IA-D07).

Ahora el tipo lo decide `tipo_sse_episodio` (mismo criterio en los dos
caminos): `alerta` si el episodio abierto después de la lectura no es el que
estaba abierto antes. La reapertura de un episodio NO atendido dentro de la
ventana sigue llegando como `alerta` (antes no había ninguno abierto) y la
excursión continua del mismo episodio sigue siendo `episodio_actualizado`.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai import gobierno
from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_HISTORIAL_INSUFICIENTE,
    CALIDAD_OK,
)
from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.interface import main
from src.interface.api.mappers import tipo_sse_episodio
from tests.conftest import auth_header
from tests.integration.test_ia_d18_salvaguarda_sin_clase import _atender, _Notificaciones, _ServicioPreventivo

TIPOS_EPISODIO = ("alerta", "episodio_actualizado", "recuperacion")


class _Espia:
    """Broadcaster que registra (tipo, evento) de cada publicación."""

    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict]] = []

    async def publicar(self, evento, tipo="lectura"):
        self.eventos.append((tipo, evento))

    def episodios(self) -> list[tuple[str, str | None]]:
        """(tipo, episodio_alerta) de los eventos de episodio, en orden."""
        return [(t, e.get("episodio_alerta")) for t, e in self.eventos if t in TIPOS_EPISODIO]

    def vaciar(self) -> None:
        self.eventos.clear()


# ── Regla pura ───────────────────────────────────────────────────────────────


def _episodio(identificador):
    return SimpleNamespace(id=identificador)


@pytest.mark.parametrize(
    ("previo", "actual", "esperado"),
    [
        (None, None, None),  # sin episodio antes ni después: solo el evento de la lectura
        (None, "A", "alerta"),  # alerta nueva o reapertura (no había ninguno abierto)
        ("A", "A", "episodio_actualizado"),  # el mismo episodio sigue abierto
        ("A", "B", "alerta"),  # se cerró A y se abrió B (IA-D18, escalamiento, cambio de nivel)
        ("A", None, "recuperacion"),  # se cerró y no queda ninguno
    ],
)
def test_tipo_sse_episodio(previo, actual, esperado):
    assert tipo_sse_episodio(
        _episodio(previo) if previo else None, _episodio(actual) if actual else None
    ) == esperado


# ── Camino MQTT (interface/main.py) ─────────────────────────────────────────

DEVICE_MQTT = "FARM-SSE2B-01"
_SEQ = iter(range(1, 100_000))


class _Mensaje:
    def __init__(self, payload: str) -> None:
        self.topic = f"farmacias/{DEVICE_MQTT}/lecturas"
        self.payload = payload.encode("utf-8")


async def _mqtt(espia: _Espia, instante: datetime, temperatura: float | None) -> None:
    datos = {
        "device_id": DEVICE_MQTT, "timestamp": instante.isoformat(), "schema_version": 1,
        "reading_id": f"sse2b-{next(_SEQ)}", "temperatura_interna": temperatura,
        "temperatura_ambiental": 22.0, "humedad_ambiental": 55.0, "apertura_refrigerador": False,
        "estado_conectividad": "online", "time_quality": "synced",
    }
    await main._procesar_lectura_mqtt(_Mensaje(json.dumps(datos)), espia, None, _Notificaciones())


def _alertas(client, token, device: str, **filtros) -> list[dict]:
    respuesta = client.get("/api/alertas", params={"device_id": device, **filtros}, headers=auth_header(token))
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


@pytest.fixture
def ingesta_mqtt(db_session_factory, monkeypatch):
    """Manejador MQTT real. Sin argumentos: valores de producción (preventivo
    desactivado, legado experimental). Con un servicio: preventivo activo."""
    monkeypatch.setattr(main, "_session_factory", db_session_factory)

    async def _preparar(servicio: _ServicioPreventivo | None = None) -> None:
        if servicio is not None:
            monkeypatch.setenv("IA_PREVENTIVO_MODO", "activo")
            monkeypatch.setenv("IA_PREVENTIVO_VERSION", "rf-d18-1.0.0")
            monkeypatch.setenv("IA_LEGADO_MODO", "experimental")
            monkeypatch.setattr(main, "obtener_servicio_preventivo", lambda *a, **k: servicio)
        get_settings.cache_clear()
        async with db_session_factory() as session:
            await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_MQTT)
            await session.commit()

    yield _preparar
    get_settings.cache_clear()


def _inicio(hace_min: int) -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=hace_min)


async def test_mqtt_primera_excursion_es_alerta_y_la_vuelta_a_rango_recuperacion(ingesta_mqtt):
    await ingesta_mqtt()
    espia, t0 = _Espia(), _inicio(10)

    await _mqtt(espia, t0, 8.5)
    await _mqtt(espia, t0 + timedelta(minutes=1), 8.7)
    await _mqtt(espia, t0 + timedelta(minutes=2), 5.0)

    (tipo1, a1), (tipo2, a2), (tipo3, _) = espia.episodios()
    assert (tipo1, tipo2, tipo3) == ("alerta", "episodio_actualizado", "recuperacion")
    assert a1 == a2


@pytest.mark.parametrize(
    "pasos",
    [
        pytest.param([(2, None), (3, None), (7, 8.5)], id="falla_de_sensor"),
        pytest.param([(80, 9.5)], id="hueco_sin_lecturas"),
    ],
)
async def test_mqtt_alerta_pendiente_nueva_tras_cerrar_la_atendida_llega_como_alerta(
    client, token_farmaceutico, ingesta_mqtt, pasos
):
    """IA-D18 sin continuidad: el episodio ATENDIDO estaba abierto antes de la
    excursión y la excursión abre otro. Antes: `episodio_actualizado`."""
    await ingesta_mqtt()
    espia, t0 = _Espia(), _inicio(90)
    await _mqtt(espia, t0, 8.5)
    [atendida] = _alertas(client, token_farmaceutico, DEVICE_MQTT, estado="pendiente")
    _atender(client, token_farmaceutico, atendida["id"])
    espia.vaciar()

    for minutos, temperatura in pasos:
        await _mqtt(espia, t0 + timedelta(minutes=minutos), temperatura)

    [nueva] = _alertas(client, token_farmaceutico, DEVICE_MQTT, estado="pendiente")
    assert nueva["id"] != atendida["id"]
    assert espia.episodios()[-1] == ("alerta", nueva["id"])
    assert ("episodio_actualizado", nueva["id"]) not in espia.episodios()


async def test_mqtt_alerta_nueva_tras_atendida_cerrada_por_la_regla_sigue_siendo_alerta(
    client, token_farmaceutico, ingesta_mqtt
):
    """IA-D18 con lectura en rango entre medio: la regla cierra la ATENDIDA
    (`recuperacion`) y la excursión siguiente llega como `alerta`."""
    await ingesta_mqtt()
    espia, t0 = _Espia(), _inicio(9)
    await _mqtt(espia, t0, 8.5)
    [atendida] = _alertas(client, token_farmaceutico, DEVICE_MQTT, estado="pendiente")
    _atender(client, token_farmaceutico, atendida["id"])
    espia.vaciar()

    await _mqtt(espia, t0 + timedelta(minutes=2), 5.0)
    await _mqtt(espia, t0 + timedelta(minutes=7), 8.5)

    [nueva] = _alertas(client, token_farmaceutico, DEVICE_MQTT, estado="pendiente")
    assert [t for t, _ in espia.episodios()] == ["recuperacion", "alerta"]
    assert espia.episodios()[-1] == ("alerta", nueva["id"])


async def test_mqtt_excursion_continua_tras_atendida_sigue_siendo_episodio_actualizado(
    client, token_farmaceutico, ingesta_mqtt
):
    await ingesta_mqtt()
    espia, t0 = _Espia(), _inicio(9)
    await _mqtt(espia, t0, 8.5)
    [atendida] = _alertas(client, token_farmaceutico, DEVICE_MQTT, estado="pendiente")
    _atender(client, token_farmaceutico, atendida["id"])
    espia.vaciar()

    await _mqtt(espia, t0 + timedelta(seconds=30), 8.6)

    assert espia.episodios() == [("episodio_actualizado", atendida["id"])]


async def test_mqtt_reapertura_de_una_alerta_no_atendida_conserva_el_tipo_alerta(
    client, token_farmaceutico, ingesta_mqtt
):
    """Comportamiento que se conserva: dentro de la ventana, la excursión
    reabre el mismo episodio PENDIENTE (no había ninguno abierto: `alerta`)."""
    await ingesta_mqtt()
    espia, t0 = _Espia(), _inicio(10)

    await _mqtt(espia, t0, 8.5)
    await _mqtt(espia, t0 + timedelta(minutes=1), 5.0)
    await _mqtt(espia, t0 + timedelta(minutes=3), 8.5)

    [unica] = _alertas(client, token_farmaceutico, DEVICE_MQTT)
    assert unica["episodio_abierto"] is True and unica["estado"] == "pendiente"
    assert espia.episodios() == [("alerta", unica["id"]), ("recuperacion", None), ("alerta", unica["id"])]


class _ModeloConmutable(_ServicioPreventivo):
    """Preventivo activo cuya clase en rango se elige en cada paso."""

    def __init__(self) -> None:
        super().__init__(CALIDAD_OK, None, clase=NivelRiesgo.RIESGO_PREVENTIVO)

    def preventivo(self) -> None:
        self._estado, self._motivo, self._clase = CALIDAD_OK, None, NivelRiesgo.RIESGO_PREVENTIVO

    def ciego(self) -> None:
        self._estado, self._motivo, self._clase = CALIDAD_HISTORIAL_INSUFICIENTE, "ventana", None


async def test_mqtt_escalamiento_de_preventiva_a_critica_llega_como_alerta(client, token_farmaceutico, ingesta_mqtt):
    """IA-D07: la preventiva abierta escala a crítica (otro episodio). Antes:
    `episodio_actualizado`, sin aviso de la alerta crítica en el tablero."""
    modelo = _ModeloConmutable()
    await ingesta_mqtt(modelo)
    espia, t0 = _Espia(), _inicio(10)

    await _mqtt(espia, t0, 7.5)
    [preventiva] = _alertas(client, token_farmaceutico, DEVICE_MQTT)
    assert preventiva["nivel_riesgo"] == "riesgo_preventivo"
    await _mqtt(espia, t0 + timedelta(minutes=1), 8.5)

    critica = next(a for a in _alertas(client, token_farmaceutico, DEVICE_MQTT) if a["episodio_abierto"])
    assert critica["nivel_riesgo"] == "excursion_critica" and critica["id"] != preventiva["id"]
    assert espia.episodios() == [("alerta", preventiva["id"]), ("alerta", critica["id"])]


async def test_mqtt_cambio_de_nivel_de_critica_a_preventiva_llega_como_alerta(
    client, token_farmaceutico, ingesta_mqtt
):
    """IA-D07: al volver a rango con riesgo preventivo se abre una preventiva
    aparte (la crítica se cierra por cambio de nivel): es otra alerta."""
    modelo = _ModeloConmutable()
    await ingesta_mqtt(modelo)
    espia, t0 = _Espia(), _inicio(10)

    await _mqtt(espia, t0, 8.5)
    await _mqtt(espia, t0 + timedelta(minutes=1), 7.5)

    alertas = _alertas(client, token_farmaceutico, DEVICE_MQTT)
    critica = next(a for a in alertas if a["nivel_riesgo"] == "excursion_critica")
    preventiva = next(a for a in alertas if a["nivel_riesgo"] == "riesgo_preventivo")
    assert espia.episodios() == [("alerta", critica["id"]), ("alerta", preventiva["id"])]


# ── Camino HTTP (api/lecturas_router.py) ────────────────────────────────────


def _http(client, token, device: str, instante: datetime, temperatura: float | None, app) -> list:
    espia = _Espia()
    broadcaster = app.state.sse_broadcaster
    original = broadcaster.publicar

    async def espiar(evento, tipo="lectura"):
        await espia.publicar(evento, tipo)
        return await original(evento, tipo)

    broadcaster.publicar = espiar
    try:
        respuesta = client.post("/api/lecturas", json={
            "device_id": device, "timestamp": instante.isoformat(), "temperatura_interna": temperatura,
            "temperatura_ambiental": 21.0, "humedad_ambiental": 55.0, "apertura_refrigerador": False,
            "estado_conectividad": "online",
        }, headers=auth_header(token))
        assert respuesta.status_code == 201, respuesta.text
    finally:
        broadcaster.publicar = original
    return espia.episodios()


def test_http_primera_excursion_es_alerta_y_la_vuelta_a_rango_recuperacion(client, token_farmaceutico, app):
    """La vuelta a rango sin clase del modelo (pocas lecturas previas) también
    cierra el episodio: el tablero recibe `recuperacion`, como por MQTT."""
    device, t0 = f"FARM-SSE2B-H{uuid4().hex[:6]}", _inicio(10)

    [(tipo1, alerta_id)] = _http(client, token_farmaceutico, device, t0, 8.5, app)
    assert _http(client, token_farmaceutico, device, t0 + timedelta(minutes=1), 8.7, app) == [
        ("episodio_actualizado", alerta_id)
    ]
    assert _http(client, token_farmaceutico, device, t0 + timedelta(minutes=2), 5.0, app) == [("recuperacion", None)]
    assert tipo1 == "alerta"


def test_http_alerta_pendiente_nueva_tras_cerrar_la_atendida_llega_como_alerta(client, token_farmaceutico, app):
    """IA-D18 por la vía manual: hueco de 80 min tras la atención."""
    device, t0 = f"FARM-SSE2B-H{uuid4().hex[:6]}", _inicio(90)
    _http(client, token_farmaceutico, device, t0, 8.5, app)
    [atendida] = _alertas(client, token_farmaceutico, device, estado="pendiente")
    _atender(client, token_farmaceutico, atendida["id"])

    eventos = _http(client, token_farmaceutico, device, t0 + timedelta(minutes=80), 9.5, app)

    [nueva] = _alertas(client, token_farmaceutico, device, estado="pendiente")
    assert nueva["id"] != atendida["id"]
    assert eventos == [("alerta", nueva["id"])]


def test_http_excursion_continua_tras_atendida_sigue_siendo_episodio_actualizado(client, token_farmaceutico, app):
    device, t0 = f"FARM-SSE2B-H{uuid4().hex[:6]}", _inicio(9)
    _http(client, token_farmaceutico, device, t0, 8.5, app)
    [atendida] = _alertas(client, token_farmaceutico, device, estado="pendiente")
    _atender(client, token_farmaceutico, atendida["id"])

    assert _http(client, token_farmaceutico, device, t0 + timedelta(seconds=30), 8.6, app) == [
        ("episodio_actualizado", atendida["id"])
    ]


def test_http_reapertura_de_una_alerta_no_atendida_conserva_el_tipo_alerta(client, token_farmaceutico, app):
    device, t0 = f"FARM-SSE2B-H{uuid4().hex[:6]}", _inicio(10)
    _http(client, token_farmaceutico, device, t0, 8.5, app)
    _http(client, token_farmaceutico, device, t0 + timedelta(minutes=1), 5.0, app)

    eventos = _http(client, token_farmaceutico, device, t0 + timedelta(minutes=3), 8.5, app)

    [unica] = _alertas(client, token_farmaceutico, device)
    assert unica["episodio_abierto"] is True
    assert eventos == [("alerta", unica["id"])]


class _LegadoQueAnticipa(rf.RandomForestRiesgoService):
    """Legado real cuya inferencia en rango dice riesgo_preventivo."""

    def inferir(self, features):
        from src.infrastructure.ai.random_forest_service import (
            ESTADO_COMPLETADA,
            ORIGEN_MODELO,
            ResultadoInferencia,
        )
        from src.infrastructure.ai.reglas_riesgo import clasificar_por_regla

        return ResultadoInferencia(
            NivelRiesgo.RIESGO_PREVENTIVO, 0.9, ORIGEN_MODELO, ESTADO_COMPLETADA,
            probabilidades_por_clase={"normal": 0.1, "riesgo_preventivo": 0.9},
            vector_features={"temperatura_interna": features.temperatura_interna},
            nivel_regla=clasificar_por_regla(features),
        )


def test_http_escalamiento_de_preventiva_a_critica_llega_como_alerta(client, token_farmaceutico, app, monkeypatch):
    """Legado activo y aprobado (el que decide por la vía HTTP) que abre una
    preventiva; la excursión siguiente es otro episodio y llega como `alerta`."""
    monkeypatch.setattr(rf, "_instancia", _LegadoQueAnticipa())
    monkeypatch.setattr(gobierno, "modo_legado_efectivo", lambda settings: "activo")
    device, t0 = f"FARM-SSE2B-H{uuid4().hex[:6]}", _inicio(20)

    eventos = []
    for minuto in (0, 5, 10):  # HU-17: el legado necesita lecturas previas para clasificar
        eventos += _http(client, token_farmaceutico, device, t0 + timedelta(minutes=minuto), 5.0, app)
    [preventiva] = _alertas(client, token_farmaceutico, device)
    assert preventiva["nivel_riesgo"] == "riesgo_preventivo"
    assert eventos == [("alerta", preventiva["id"])]

    escalamiento = _http(client, token_farmaceutico, device, t0 + timedelta(minutes=11), 8.5, app)

    critica = next(a for a in _alertas(client, token_farmaceutico, device) if a["episodio_abierto"])
    assert critica["nivel_riesgo"] == "excursion_critica" and critica["id"] != preventiva["id"]
    assert escalamiento == [("alerta", critica["id"])]
