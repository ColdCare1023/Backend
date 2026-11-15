"""Ronda 2b: IA-D18 (P-8) junto con la vuelta atrás P-12 (/desactivar).

Con el modelo preventivo REAL (registro temporal, IA_PREVENTIVO_MODO=activo) y
la ingesta por el manejador MQTT real:

1. Con V activa, el modelo abre una alerta preventiva.
2. POST /desactivar: no queda versión activa, `/health/detalle` dice
   `modelo_ia.preventivo.motivo_codigo = sin_version_activa_registrada` con
   `version_activa_registrada = null`, y el servicio deja de cargar el modelo:
   cada lectura en rango sale con MODELO_NO_DISPONIBLE.
3. Sin modelo que decida, `_normal_confirmado` vuelve a la regla 2-8 °C: las
   lecturas en rango cuentan para la ventana de normalización y la preventiva
   abierta antes de la vuelta atrás se cierra (`ventana_normalizacion_cumplida`).
   Si siguiera exigiendo la clase de un modelo que ya no está, no se cerraría
   nunca.
4. La secuencia IA-D18 (excursión → atendida → en rango → excursión) sigue
   abriendo una alerta PENDIENTE nueva con su aviso después de desactivar.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
import sklearn
from sqlalchemy import select

from src.application.use_cases.generar_alerta import CIERRE_NORMALIZACION, AvisoExcursion
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.prediccion_preventiva import servicio as modulo_servicio
from src.infrastructure.ai.prediccion_preventiva.configuracion import CALIDAD_MODELO_NO_DISPONIBLE
from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_ACTIVO, ServicioPrediccionPreventiva
from src.infrastructure.ai.random_forest_service import RandomForestRiesgoService
from src.infrastructure.config import get_settings
from src.infrastructure.database.models import PrediccionPreventivaModel, TraceabilityRecordModel
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.interface import main
from tests.conftest import auth_header
from tests.ia_preventiva import registrar_preventivo

VERSION = "rf-preventivo-r2b-d18-1.0.0"
DEVICE_ID = "FARM-R2B-D18-01"
_VARIABLES = ("IA_PREVENTIVO_MODO", "IA_PREVENTIVO_VERSION", "IA_PREVENTIVO_SHA256", "IA_LEGADO_MODO")
_METRICAS = {
    "f1_macro": 0.88, "incidentes_evaluables": 12, "sensibilidad_incidente": 0.92,
    "ic95_inferior_sensibilidad": 0.62, "falsas_alarmas_por_dia_sensor": 1.1,
    "mediana_anticipacion_min": 9.0, "ap_random_forest": 0.71, "ap_mejor_baseline": 0.55,
}
_SEQ = iter(range(1, 100_000))


class _Mensaje:
    def __init__(self, payload: str) -> None:
        self.topic = f"farmacias/{DEVICE_ID}/lecturas"
        self.payload = payload.encode("utf-8")


class _BroadcasterMudo:
    async def publicar(self, evento, tipo="lectura"):
        return None


class _Notificaciones:
    def __init__(self) -> None:
        self.avisos: list = []

    def encolar(self, aviso) -> bool:
        self.avisos.append(aviso)
        return True

    def encolar_todos(self, avisos) -> None:
        for aviso in avisos:
            self.encolar(aviso)


@pytest.fixture
def preventivo_real(tmp_path, monkeypatch, db_session_factory):
    """V registrada y aprobada en un registro temporal, con la configuración
    de la fase A (modo activo con la versión y el SHA-256 de V)."""
    sha = registrar_preventivo(tmp_path, VERSION)
    anteriores = {k: os.environ.get(k) for k in _VARIABLES}
    os.environ.update(
        IA_PREVENTIVO_MODO="activo", IA_PREVENTIVO_VERSION=VERSION, IA_PREVENTIVO_SHA256=sha,
        IA_LEGADO_MODO="experimental",
    )
    get_settings.cache_clear()
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)
    monkeypatch.setattr(modulo_servicio, "_instancia", servicio)
    monkeypatch.setattr(rf, "_instancia", RandomForestRiesgoService())
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    try:
        yield servicio, sha
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        get_settings.cache_clear()


def _activar(client, token, sha) -> None:
    cuerpo = {
        "version": VERSION, "model_hash": sha, "feature_schema_version": "termico_minimo",
        "dataset_version": "corridas-2026-11", "scikit_learn_version": sklearn.__version__,
        "python_version": "3.12.13", "trained_at": "2026-11-01T00:00:00Z", "horizonte_min": 30,
        "metricas": _METRICAS, "particiones": {"entrenamiento": "C1,C2", "prueba": "C3", "separacion_min": 30},
        "activar": True,
    }
    respuesta = client.post("/api/ia/modelo/versiones", json=cuerpo, headers=auth_header(token))
    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["activa"] is True


def _desactivar(client, token) -> None:
    respuesta = client.post(
        f"/api/ia/modelo/versiones/{VERSION}/desactivar",
        json={"motivo": "Vuelta atrás R-A: falsas alarmas sobre el umbral acordado"},
        headers=auth_header(token),
    )
    assert respuesta.status_code == 200, respuesta.text


async def _mqtt(notificaciones, instante: datetime, temperatura: float | None) -> None:
    datos = {
        "device_id": DEVICE_ID, "timestamp": instante.isoformat(), "schema_version": 1,
        "reading_id": f"r2b-d18-{next(_SEQ)}", "temperatura_interna": temperatura,
        "temperatura_ambiental": 22.0, "humedad_ambiental": 50.0, "apertura_refrigerador": False,
        "estado_conectividad": "online", "time_quality": "synced",
    }
    await main._procesar_lectura_mqtt(_Mensaje(json.dumps(datos)), _BroadcasterMudo(), None, notificaciones)


def _alertas(client, token, **filtros) -> list[dict]:
    respuesta = client.get("/api/alertas", params={"device_id": DEVICE_ID, **filtros}, headers=auth_header(token))
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


async def _crear_dispositivo(factory) -> None:
    async with factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()


async def _ultima_lectura(factory):
    async with factory() as session:
        recientes = await SQLAlchemyLecturaRepository(session).listar_recientes_por_device(DEVICE_ID, limite=1)
    return recientes[0]


async def _estado_ultima_prediccion(factory) -> str:
    async with factory() as session:
        filas = (await session.execute(select(PrediccionPreventivaModel))).scalars().all()
    return max(filas, key=lambda f: f.timestamp_inferencia).estado


async def _cierres(factory) -> list[dict]:
    async with factory() as session:
        filas = (
            await session.execute(select(TraceabilityRecordModel).order_by(TraceabilityRecordModel.chain_seq))
        ).scalars().all()
    return [f.payload for f in filas if f.tipo_evento == "ALERTA_CERRADA" and f.payload["device_id"] == DEVICE_ID]


async def _abrir_preventiva_con_el_modelo(client, token, factory, t0: datetime) -> dict:
    """20 min estables en 5 °C y una subida de 0,12 °C/min sin salir de 2-8 °C:
    el modelo de prueba (riesgo si la pendiente de 5 min supera 0,05 °C/min)
    abre una alerta preventiva."""
    notificaciones = _Notificaciones()
    temperaturas = [5.0] * 20 + [round(5.0 + 0.12 * i, 2) for i in range(1, 6)]
    for minuto, temperatura in enumerate(temperaturas):
        await _mqtt(notificaciones, t0 + timedelta(minutes=minuto), temperatura)
    [preventiva] = _alertas(client, token)
    assert (preventiva["nivel_riesgo"], preventiva["episodio_abierto"]) == ("riesgo_preventivo", True)
    ultima = await _ultima_lectura(factory)
    assert ultima.modelo_version == VERSION and ultima.riesgo_efectivo == NivelRiesgo.RIESGO_PREVENTIVO
    return preventiva


@pytest.mark.parametrize("legado", ["experimental", "desactivado"])
async def test_tras_desactivar_la_preventiva_abierta_se_cierra_por_la_regla(
    client, token_admin, token_farmaceutico, preventivo_real, db_session_factory, legado
):
    """`desactivado` es el R-A elegido (sección 6.3): sin el legado nada da una
    clase y la lectura en rango queda «Sin clasificación IA»; aun así cuenta
    para la ventana porque ningún modelo decide. Con `experimental` el legado
    deja su clase como evidencia marcada."""
    servicio, sha = preventivo_real
    os.environ["IA_LEGADO_MODO"] = legado
    get_settings.cache_clear()
    await _crear_dispositivo(db_session_factory)
    _activar(client, token_admin, sha)
    t0 = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=60)
    preventiva = await _abrir_preventiva_con_el_modelo(client, token_farmaceutico, db_session_factory, t0)

    _desactivar(client, token_admin)

    modelo_ia = client.get("/health/detalle", headers=auth_header(token_admin)).json()["modelo_ia"]
    assert modelo_ia["preventivo"]["motivo_codigo"] == "sin_version_activa_registrada"
    assert modelo_ia["version_activa_registrada"] is None
    assert modelo_ia["preventivo"]["disponible"] is False
    assert modelo_ia["en_uso"] == {"modelo": "preventivo", "version": VERSION, "disponible": False, "aprobado": False}
    assert servicio.disponible is False

    # Vuelta a 5 °C, estable: 11 minutos de lecturas en rango sin modelo.
    notificaciones = _Notificaciones()
    for minuto in range(26, 38):
        await _mqtt(notificaciones, t0 + timedelta(minutes=minuto), 5.0)
        if minuto == 26:
            # El servicio ya no carga el modelo: la regla decide (línea base).
            assert await _estado_ultima_prediccion(db_session_factory) == CALIDAD_MODELO_NO_DISPONIBLE
            en_rango = await _ultima_lectura(db_session_factory)
            assert en_rango.riesgo_efectivo == NivelRiesgo.NORMAL
            assert en_rango.modelo_version != VERSION
            if legado == "desactivado":
                assert en_rango.estado_riesgo() == "sin_clasificacion_ia"

    [cerrada] = _alertas(client, token_farmaceutico)
    assert cerrada["id"] == preventiva["id"]
    assert cerrada["episodio_abierto"] is False
    [cierre] = await _cierres(db_session_factory)
    assert cierre["alerta_id"] == preventiva["id"]
    assert cierre["motivo_cierre"] == CIERRE_NORMALIZACION
    assert notificaciones.avisos == []


async def test_tras_desactivar_la_secuencia_d18_abre_una_alerta_pendiente_nueva(
    client, token_admin, token_farmaceutico, preventivo_real, db_session_factory
):
    """IA-D18 después de la vuelta atrás: excursión → atendida → en rango (sin
    modelo: MODELO_NO_DISPONIBLE) → excursión = 1 alerta PENDIENTE nueva y 1 aviso."""
    _, sha = preventivo_real
    await _crear_dispositivo(db_session_factory)
    _activar(client, token_admin, sha)
    _desactivar(client, token_admin)
    notificaciones = _Notificaciones()
    inicio = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=9)

    await _mqtt(notificaciones, inicio, 8.5)
    [primera] = _alertas(client, token_farmaceutico, estado="pendiente")
    assert client.patch(
        f"/api/alertas/{primera['id']}/revisar", headers=auth_header(token_farmaceutico)
    ).status_code == 200
    assert client.post(
        f"/api/alertas/{primera['id']}/acciones-correctivas",
        json={
            "decision": "Mantener el lote en cuarentena",
            "descripcion": "Se cerró la puerta y se trasladó el lote al equipo de respaldo.",
            "justificacion": "Excursión breve por encima de 8 °C.",
        },
        headers=auth_header(token_farmaceutico),
    ).status_code == 201
    avisos_previos = len(notificaciones.avisos)

    await _mqtt(notificaciones, inicio + timedelta(minutes=2), 5.0)
    assert await _estado_ultima_prediccion(db_session_factory) == CALIDAD_MODELO_NO_DISPONIBLE
    await _mqtt(notificaciones, inicio + timedelta(minutes=7), 8.5)

    [nueva] = _alertas(client, token_farmaceutico, estado="pendiente")
    assert nueva["id"] != primera["id"]
    assert nueva["nivel_riesgo"] == NivelRiesgo.EXCURSION_CRITICA.value
    avisos = [a for a in notificaciones.avisos[avisos_previos:] if isinstance(a, AvisoExcursion)]
    assert [str(a.alerta_id) for a in avisos] == [nueva["id"]]
    atendida = next(a for a in _alertas(client, token_farmaceutico) if a["id"] == primera["id"])
    assert (atendida["estado"], atendida["episodio_abierto"]) == ("atendida", False)
