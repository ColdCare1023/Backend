"""IA-D18 (P-8, seguridad, sin excepción) y casos de seguridad D07-D10/D19 del
plan de pruebas de IA.

Defecto: con la IA preventiva en modo activo, una lectura VÁLIDA dentro de
2-8 °C para la que el modelo no da clase (inferencia omitida por historial
insuficiente o reloj sin sincronizar) quedaba con `riesgo_efectivo=None`. Sin
riesgo efectivo no se llamaba a `generar_alerta`, el episodio crítico ATENDIDO
no se cerraba por recuperación y la excursión siguiente se sumaba a él: ninguna
alerta PENDIENTE nueva ni correo. Lo mismo pasaba con el legado activo cuando
omitía la clasificación.

Regla (la decide siempre la regla 2-8 °C cuando el modelo no clasifica, en
cualquier modo):
- temperatura válida en rango → `normal` (el tablero sigue mostrando «Sin
  clasificación IA», HU-34 esc. 4);
- temperatura válida fuera de rango → `excursion_critica`, diga lo que diga el
  modelo;
- temperatura nula o inválida → `None` (camino de falla de sensor, sin cambios).

Las lecturas entran por el manejador MQTT real (`_procesar_lectura_mqtt`), con
IA_PREVENTIVO_MODO=activo y un doble del servicio preventivo; reconocer y
registrar la acción correctiva pasan por la API, como en IA-D10."""

import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sqlalchemy import select

from src.application.use_cases.generar_alerta import CIERRE_NORMALIZACION, AvisoExcursion
from src.domain.entities.prediccion_preventiva import PrediccionPreventiva
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_EXCURSION_EN_CURSO,
    CALIDAD_FALLO_INFERENCIA,
    CALIDAD_HISTORIAL_INSUFICIENTE,
    CALIDAD_MODELO_NO_DISPONIBLE,
    CALIDAD_OK,
    CALIDAD_SENSOR_FAULT,
    ESTADO_APROBADO,
    ESTADO_VALIDADO_OFFLINE,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import CONJUNTO_TERMICO_AMBIENTE, nombres_features
from src.infrastructure.ai.prediccion_preventiva.servicio import (
    MODO_ACTIVO,
    MODO_DESACTIVADO,
    ServicioPrediccionPreventiva,
)
from src.infrastructure.config import get_settings
from src.infrastructure.database.models import TraceabilityRecordModel
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.interface import main
from tests.conftest import auth_header

DEVICE_ID = "FARM-IAD18-01"
TOPIC = f"farmacias/{DEVICE_ID}/lecturas"
CONFIG_PREVENTIVA = ConfiguracionPreventiva()


class _Mensaje:
    def __init__(self, payload: str) -> None:
        self.topic = TOPIC
        self.payload = payload.encode("utf-8")


class _BroadcasterMudo:
    async def publicar(self, evento, tipo="lectura"):
        return None


class _Notificaciones:
    """Doble del despachador: registra lo que la ingesta encola tras el COMMIT."""

    def __init__(self) -> None:
        self.avisos: list = []

    def encolar(self, aviso) -> bool:
        self.avisos.append(aviso)
        return True

    def encolar_todos(self, avisos) -> None:
        for aviso in avisos:
            self.encolar(aviso)

    async def detener(self, timeout: float = 5.0) -> None:
        return None


class _ServicioPreventivo:
    """Doble de `ServicioPrediccionPreventiva` con una versión activa y aprobada.

    Fuera de 2-8 °C o sin temperatura válida responde como el servicio real
    (EXCURSION_EN_CURSO / SENSOR_FAULT). Dentro de rango devuelve el estado
    configurado: así se reproduce cada motivo de omisión sin depender de un
    artefacto entrenado. `siempre` fuerza la respuesta también fuera de rango
    (un modelo que dice «normal» a 9 °C)."""

    def __init__(
        self,
        estado: str = CALIDAD_HISTORIAL_INSUFICIENTE,
        motivo: str | None = "ventana_15min_sin_cobertura_suficiente",
        *,
        modo: str = MODO_ACTIVO,
        clase: NivelRiesgo | None = None,
        retardo_s: float = 0.0,
        excepcion: bool = False,
        siempre: bool = False,
    ) -> None:
        self.modo = modo
        self._estado = estado
        self._motivo = motivo
        self._clase = clase
        self._retardo_s = retardo_s
        self._excepcion = excepcion
        self._siempre = siempre

    @property
    def habilitado(self) -> bool:
        return self.modo != MODO_DESACTIVADO

    def _resultado(self, lectura, estado: str, motivo: str | None, clase: NivelRiesgo | None = None):
        return PrediccionPreventiva(
            device_id=lectura.device_id, timestamp_inferencia=lectura.timestamp, modo=self.modo,
            estado=estado, motivo=motivo, clase=clase, horizonte_min=15, modelo_version="rf-d18-1.0.0",
            puntuacion=0.1 if clase is not None else None, umbral=0.5 if clase is not None else None,
        )

    def predecir(self, lectura, historial):
        if not self._siempre:
            if lectura.temperatura_interna is None:
                return self._resultado(lectura, CALIDAD_SENSOR_FAULT, "sensor_interno_ausente_o_invalido")
            if lectura.es_excursion_confirmada():
                return self._resultado(lectura, CALIDAD_EXCURSION_EN_CURSO, "excursion_actual_regla_determinista")
        if self._retardo_s:
            time.sleep(self._retardo_s)  # hilo no cancelable, como el modelo real
        if self._excepcion:
            raise RuntimeError("fallo del modelo preventivo")
        return self._resultado(lectura, self._estado, self._motivo, self._clase)


@pytest.fixture
def ingesta(db_session_factory, monkeypatch):
    """Manejador MQTT con IA_PREVENTIVO_MODO=activo y el servicio que se le pase.
    `get_settings` está cacheado: se limpia tras cada cambio de entorno."""
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    monkeypatch.setenv("IA_PREVENTIVO_MODO", "activo")
    monkeypatch.setenv("IA_PREVENTIVO_VERSION", "rf-d18-1.0.0")

    def _configurar(
        servicio: _ServicioPreventivo, legado: str = "experimental", timeout_s: float | None = None
    ) -> None:
        monkeypatch.setenv("IA_LEGADO_MODO", legado)
        if servicio.modo == MODO_DESACTIVADO:
            monkeypatch.setenv("IA_PREVENTIVO_MODO", "desactivado")
        if timeout_s is not None:
            monkeypatch.setenv("IA_INFERENCIA_TIMEOUT_S", str(timeout_s))
        monkeypatch.setattr(main, "obtener_servicio_preventivo", lambda *a, **k: servicio)
        get_settings.cache_clear()
        ajustes = get_settings()
        assert (ajustes.ia_preventivo_modo, ajustes.ia_legado_modo) == (
            "desactivado" if servicio.modo == MODO_DESACTIVADO else "activo", legado
        )

    yield _configurar
    get_settings.cache_clear()


_SEQ = iter(range(1, 100_000))


async def _mqtt(
    notificaciones: _Notificaciones,
    instante: datetime,
    temperatura: float | None,
    time_quality: str = "synced",
    ambiente: float | None = 22.0,
    humedad: float | None = 55.0,
) -> None:
    datos = {
        "device_id": DEVICE_ID,
        "timestamp": instante.isoformat(),
        "schema_version": 1,
        "reading_id": f"iad18-{next(_SEQ)}",
        "temperatura_interna": temperatura,
        "temperatura_ambiental": ambiente,
        "humedad_ambiental": humedad,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
        "time_quality": time_quality,
    }
    await main._procesar_lectura_mqtt(_Mensaje(json.dumps(datos)), _BroadcasterMudo(), None, notificaciones)


def _alertas(client, token, **filtros) -> list[dict]:
    respuesta = client.get("/api/alertas", params={"device_id": DEVICE_ID, **filtros}, headers=auth_header(token))
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _atender(client, token, alerta_id: str) -> None:
    """IA-D10 paso 2: reconocer (QF) y registrar la acción correctiva → ATENDIDA."""
    revisar = client.patch(f"/api/alertas/{alerta_id}/revisar", headers=auth_header(token))
    assert revisar.status_code == 200, revisar.text
    accion = client.post(
        f"/api/alertas/{alerta_id}/acciones-correctivas",
        json={
            "decision": "Mantener el lote en cuarentena",
            "descripcion": "Se cerró la puerta y se trasladó el lote al equipo de respaldo.",
            "justificacion": "Excursión breve por encima de 8 °C.",
        },
        headers=auth_header(token),
    )
    assert accion.status_code == 201, accion.text


async def _lecturas(factory) -> list:
    async with factory() as session:
        recientes = await SQLAlchemyLecturaRepository(session).listar_recientes_por_device(DEVICE_ID, limite=50)
    return sorted(recientes, key=lambda x: x.timestamp)


@dataclass
class _ResultadoD18:
    atendida_id: str
    pendientes: list[dict]
    avisos: list
    en_rango: object  # LecturaTermica de la vuelta a rango


async def _secuencia_d18(client, token_qf, factory, time_quality: str = "synced") -> _ResultadoD18:
    """IA-D18: excursión → reconocer + acción (ATENDIDA) → lectura en rango sin
    clase del modelo → excursión nueva antes de la ventana de normalización."""
    async with factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    notificaciones = _Notificaciones()
    inicio = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=9)

    await _mqtt(notificaciones, inicio, 8.5, time_quality)
    [primera] = _alertas(client, token_qf, estado="pendiente")
    _atender(client, token_qf, primera["id"])
    avisos_previos = len(notificaciones.avisos)

    await _mqtt(notificaciones, inicio + timedelta(minutes=2), 5.0, time_quality)
    await _mqtt(notificaciones, inicio + timedelta(minutes=7), 8.5, time_quality)

    en_rango = next(x for x in await _lecturas(factory) if x.temperatura_interna == 5.0)
    return _ResultadoD18(
        atendida_id=primera["id"],
        pendientes=_alertas(client, token_qf, estado="pendiente"),
        avisos=notificaciones.avisos[avisos_previos:],
        en_rango=en_rango,
    )


def _afirmar_alerta_nueva(resultado: _ResultadoD18) -> None:
    assert len(resultado.pendientes) == 1, resultado.pendientes
    nueva = resultado.pendientes[0]
    assert nueva["id"] != resultado.atendida_id
    assert nueva["nivel_riesgo"] == NivelRiesgo.EXCURSION_CRITICA.value
    assert nueva["episodio_abierto"] is True
    assert [str(a.alerta_id) for a in resultado.avisos] == [nueva["id"]]
    assert resultado.avisos[0].nivel == NivelRiesgo.EXCURSION_CRITICA


async def test_ia_d18_excursion_nueva_tras_atendida_con_inferencia_omitida_abre_alerta_pendiente(
    client, token_farmaceutico, db_session_factory, ingesta
):
    """Caso IA-D18 del plan: modo activo, HISTORIAL_INSUFICIENTE en la vuelta a rango."""
    ingesta(_ServicioPreventivo(CALIDAD_HISTORIAL_INSUFICIENTE, "ventana_15min_sin_cobertura_suficiente"))

    resultado = await _secuencia_d18(client, token_farmaceutico, db_session_factory)

    _afirmar_alerta_nueva(resultado)
    # La vuelta a rango la decidió la regla: normal para las alertas, pero el
    # tablero no la presenta como una clasificación del modelo (HU-34 esc. 4).
    en_rango = resultado.en_rango
    assert en_rango.estado_inferencia == "omitida"
    assert en_rango.nivel_riesgo is None
    assert en_rango.riesgo_efectivo == NivelRiesgo.NORMAL
    assert en_rango.estado_riesgo() == "sin_clasificacion_ia"
    # La alerta atendida se cerró por recuperación y no absorbió la excursión nueva.
    atendida = next(a for a in _alertas(client, token_farmaceutico) if a["id"] == resultado.atendida_id)
    assert atendida["estado"] == "atendida"
    assert atendida["episodio_abierto"] is False
    assert atendida["temperatura_pico"] == 8.5


_ESCENARIOS_OMISION = {
    # Preventivo activo que REEMPLAZA la clasificación sin dar clase.
    "historial_insuficiente_ventana": (
        _ServicioPreventivo(CALIDAD_HISTORIAL_INSUFICIENTE, "ventana_15min_sin_cobertura_suficiente"), "experimental"
    ),
    "historial_insuficiente_reloj": (
        _ServicioPreventivo(CALIDAD_HISTORIAL_INSUFICIENTE, "reloj_no_sincronizado"), "experimental"
    ),
    "historial_insuficiente_legado_activo": (
        _ServicioPreventivo(CALIDAD_HISTORIAL_INSUFICIENTE, "ventana_15min_sin_cobertura_suficiente"), "activo"
    ),
    # Preventivo sin modelo o con fallo interno: decide el legado.
    "modelo_no_disponible_legado_experimental": (
        _ServicioPreventivo(CALIDAD_MODELO_NO_DISPONIBLE, "modelo_no_cargado"), "experimental"
    ),
    "modelo_no_disponible_legado_activo": (
        _ServicioPreventivo(CALIDAD_MODELO_NO_DISPONIBLE, "modelo_no_cargado"), "activo"
    ),
    "fallo_inferencia_legado_activo": (
        _ServicioPreventivo(CALIDAD_FALLO_INFERENCIA, "excepcion:ValueError"), "activo"
    ),
    # Excepción y tope de tiempo de la llamada al servicio.
    "excepcion_en_predecir": (_ServicioPreventivo(excepcion=True), "activo"),
    "tiempo_agotado": (_ServicioPreventivo(retardo_s=0.6), "activo"),
    # Sin preventivo: el legado activo omite por historial insuficiente.
    "solo_legado_activo": (_ServicioPreventivo(modo=MODO_DESACTIVADO), "activo"),
}


@pytest.mark.parametrize("escenario", list(_ESCENARIOS_OMISION))
async def test_toda_omision_de_clase_deja_que_la_regla_decida_y_la_excursion_nueva_alerta(
    client, token_farmaceutico, db_session_factory, ingesta, escenario
):
    servicio, legado = _ESCENARIOS_OMISION[escenario]
    ingesta(servicio, legado, timeout_s=0.2 if escenario == "tiempo_agotado" else None)
    # IA-D12/D18: con el reloj sin sincronizar, la telemetría llega `unsynced`.
    time_quality = "unsynced" if escenario == "historial_insuficiente_reloj" else "synced"

    resultado = await _secuencia_d18(client, token_farmaceutico, db_session_factory, time_quality)

    motivo = {
        "tiempo_agotado": "preventivo_timeout_inferencia",
        "excepcion_en_predecir": "preventivo_excepcion_inferencia",
    }.get(escenario)
    if motivo is not None:  # el fallo inyectado ocurrió de verdad
        assert (resultado.en_rango.estado_inferencia, resultado.en_rango.motivo_no_inferencia) == ("fallida", motivo)
    assert resultado.en_rango.nivel_riesgo is None
    assert resultado.en_rango.riesgo_efectivo == NivelRiesgo.NORMAL
    assert resultado.en_rango.estado_riesgo() == "sin_clasificacion_ia"
    _afirmar_alerta_nueva(resultado)


def _registrar_modelo_ambiente_aprobado(raiz) -> tuple[str, str]:
    """Modelo `termico_ambiente` aprobado con la CLI de registro. Solo varía
    `temp_actual` al entrenar, así que a 5 °C siempre da «normal» (no abre
    alertas preventivas que ensucien la secuencia)."""
    version = "rf-hu17-ambiente-1.0.0"
    nombres = nombres_features(CONFIG_PREVENTIVA, CONJUNTO_TERMICO_AMBIENTE)
    x = np.zeros((40, len(nombres)))
    x[:20, 0], x[20:, 0] = np.linspace(0, 10, 20), np.linspace(90, 100, 20)
    modelo = RandomForestClassifier(n_estimators=5, random_state=0).fit(x, np.repeat([0, 1], 20))
    registro.registrar(modelo, {
        "version": version, "estado": "EXPERIMENTAL", "conjunto_features": CONJUNTO_TERMICO_AMBIENTE,
        "features": nombres, "configuracion": CONFIG_PREVENTIVA.como_dict(), "umbral_decision": 0.5,
        "puntuacion_calibrada": False, "versiones": {"scikit_learn": sklearn.__version__},
    }, raiz)
    registro.cambiar_estado(version, ESTADO_VALIDADO_OFFLINE, "prueba", "qa", None, raiz)
    evidencia = raiz / "exp_d.json"
    evidencia.write_text("{}", encoding="utf-8")
    registro.cambiar_estado(version, ESTADO_APROBADO, "prueba", "qa", evidencia, raiz)
    return version, registro.leer_manifiesto(version, raiz)["artefacto"]["sha256"]


async def test_hu17_sin_sht31_el_modelo_con_contexto_ambiental_omite_y_decide_la_regla(
    client, token_farmaceutico, db_session_factory, ingesta, tmp_path
):
    """HU-17 esc. 1 + IA-D18, con el servicio preventivo REAL y un modelo
    `termico_ambiente` aprobado y activo (P0-4, arreglo A):

    - con el SHT31 el modelo clasifica (antes toda lectura quedaba omitida con
      `ventana_15min_sin_cobertura_suficiente`);
    - si el SHT31 deja de enviar dato, la lectura en rango queda omitida con
      `ventana_15min_sin_contexto_ambiental` y la regla 2-8 °C la deja normal;
    - la excursión siguiente es crítica y abre una alerta PENDIENTE con su aviso.
    """
    version, sha = _registrar_modelo_ambiente_aprobado(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, version, sha, raiz=tmp_path, version_activa=(version, sha))
    assert servicio.disponible, servicio.motivo_rechazo()
    ingesta(servicio)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    notificaciones = _Notificaciones()
    inicio = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=22)

    for minuto in range(18):  # 18 min a 5 °C con el SHT31: cubre la ventana de 15 min
        await _mqtt(notificaciones, inicio + timedelta(minutes=minuto), 5.0)
    for minuto in (18, 19, 20):  # el SHT31 deja de enviar dato
        await _mqtt(notificaciones, inicio + timedelta(minutes=minuto), 5.0, ambiente=None, humedad=None)
    await _mqtt(notificaciones, inicio + timedelta(minutes=21), 8.5, ambiente=None, humedad=None)

    lecturas = await _lecturas(db_session_factory)
    assert len(lecturas) == 22
    con_sht31 = lecturas[17]
    assert (con_sht31.estado_inferencia, con_sht31.origen_clasificacion) == ("completada", rf.ORIGEN_MODELO)
    assert con_sht31.nivel_riesgo == NivelRiesgo.NORMAL
    assert con_sht31.modelo_version == version
    assert con_sht31.vector_features_ia["humedad_actual"] == 55.0
    # La primera sin SHT31 aún tiene, en sus últimos 90 s, la lectura anterior con SHT31.
    assert lecturas[18].estado_inferencia == "completada"
    for sin_sht31 in lecturas[19:21]:
        assert sin_sht31.estado_inferencia == "omitida"
        assert sin_sht31.motivo_no_inferencia == "ventana_15min_sin_contexto_ambiental"
        assert sin_sht31.origen_clasificacion == rf.ORIGEN_DATO_INSUFICIENTE
        assert sin_sht31.nivel_riesgo is None
        assert sin_sht31.riesgo_efectivo == NivelRiesgo.NORMAL  # IA-D18: decide la regla 2-8 °C
        assert sin_sht31.estado_riesgo() == "sin_clasificacion_ia"
    excursion = lecturas[21]
    assert excursion.riesgo_efectivo == NivelRiesgo.EXCURSION_CRITICA
    [alerta] = _alertas(client, token_farmaceutico, estado="pendiente")
    assert alerta["nivel_riesgo"] == NivelRiesgo.EXCURSION_CRITICA.value
    assert [str(a.alerta_id) for a in notificaciones.avisos if isinstance(a, AvisoExcursion)] == [alerta["id"]]


async def test_legado_activo_con_modelo_rechazado_503_tambien_cae_en_la_regla(
    client, token_farmaceutico, db_session_factory, ingesta, monkeypatch, tmp_path
):
    """Modelo legado alterado (la API responde 503) y preventivo sin modelo: la
    vuelta a rango sigue siendo normal y la excursión nueva se avisa."""
    modelo, metadata = tmp_path / "random_forest_termico.pkl", tmp_path / "model_metadata.json"
    shutil.copyfile(rf.DEFAULT_MODEL_PATH, modelo)
    shutil.copyfile(rf.DEFAULT_METADATA_PATH, metadata)
    with modelo.open("ab") as archivo:
        archivo.write(b"alterado")
    monkeypatch.setattr(rf, "_instancia", rf.RandomForestRiesgoService(model_path=modelo, metadata_path=metadata))
    ingesta(_ServicioPreventivo(CALIDAD_MODELO_NO_DISPONIBLE, "modelo_no_cargado"), "activo")
    assert client.get("/api/ia/modelo", headers=auth_header(token_farmaceutico)).status_code == 503

    resultado = await _secuencia_d18(client, token_farmaceutico, db_session_factory)

    assert resultado.en_rango.riesgo_efectivo == NivelRiesgo.NORMAL
    _afirmar_alerta_nueva(resultado)


@pytest.mark.parametrize("legado", ["experimental", "activo"])
@pytest.mark.parametrize("temperatura", [9.0, 1.9])
async def test_el_modelo_dice_normal_fuera_de_rango_y_la_alerta_critica_sale_igual(
    client, token_farmaceutico, db_session_factory, ingesta, legado, temperatura
):
    """IA-D07/D08/D09: un modelo activo que responde «normal» a 9,0 °C (o a 1,9 °C)
    no rebaja la regla: alerta crítica PENDIENTE y un aviso encolado."""
    ingesta(_ServicioPreventivo(CALIDAD_OK, None, clase=NivelRiesgo.NORMAL, siempre=True), legado)
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    notificaciones = _Notificaciones()

    await _mqtt(notificaciones, datetime.now(tz=timezone.utc).replace(microsecond=0), temperatura)

    [lectura] = await _lecturas(db_session_factory)
    assert lectura.nivel_riesgo == NivelRiesgo.NORMAL  # la clase del modelo queda como evidencia
    assert lectura.excursion_confirmada is True
    assert lectura.riesgo_efectivo == NivelRiesgo.EXCURSION_CRITICA
    [alerta] = _alertas(client, token_farmaceutico, estado="pendiente")
    assert alerta["nivel_riesgo"] == NivelRiesgo.EXCURSION_CRITICA.value
    assert [str(a.alerta_id) for a in notificaciones.avisos] == [alerta["id"]]


async def test_sin_temperatura_valida_el_riesgo_queda_sin_resolver_como_antes(
    client, token_farmaceutico, db_session_factory, ingesta
):
    """IA-D11/D19: la falla de sensor no se convierte en normal ni en excursión."""
    ingesta(_ServicioPreventivo(CALIDAD_HISTORIAL_INSUFICIENTE, "ventana_15min_sin_cobertura_suficiente"))
    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    notificaciones = _Notificaciones()
    inicio = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=3)

    await _mqtt(notificaciones, inicio, 5.0)
    await _mqtt(notificaciones, inicio + timedelta(minutes=1), None)

    en_rango, sin_sensor = await _lecturas(db_session_factory)
    assert en_rango.riesgo_efectivo == NivelRiesgo.NORMAL
    assert sin_sensor.temperatura_interna is None
    assert sin_sensor.riesgo_efectivo is None
    assert sin_sensor.excursion_confirmada is False
    assert sin_sensor.estado_riesgo() == "falla_sensor"
    assert _alertas(client, token_farmaceutico) == []
    assert notificaciones.avisos == []


# ── Continuidad tras la atención y ventana preventiva sin clase (re-verificación) ──


async def _cerradas(factory) -> list[dict]:
    """Payloads ALERTA_CERRADA del dispositivo, en orden de la cadena."""
    async with factory() as session:
        filas = (
            await session.execute(select(TraceabilityRecordModel).order_by(TraceabilityRecordModel.chain_seq))
        ).scalars().all()
    return [f.payload for f in filas if f.tipo_evento == "ALERTA_CERRADA" and f.payload["device_id"] == DEVICE_ID]


async def _atendida_y_despues(
    client, token_qf, factory, hace_min: int, pasos: list[tuple[float, float | None]]
) -> tuple[str, list, list[dict]]:
    """Excursión de 8,5 °C hace `hace_min` minutos → ATENDIDA → `pasos`
    (minutos desde la excursión, temperatura). Devuelve el id de la atendida,
    los avisos de excursión encolados después de atenderla y las pendientes."""
    async with factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    notificaciones = _Notificaciones()
    inicio = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=hace_min)

    await _mqtt(notificaciones, inicio, 8.5)
    [primera] = _alertas(client, token_qf, estado="pendiente")
    _atender(client, token_qf, primera["id"])
    avisos_previos = len(notificaciones.avisos)

    for minutos, temperatura in pasos:
        await _mqtt(notificaciones, inicio + timedelta(minutes=minutos), temperatura)

    # Los avisos del incidente `sensor_fault` también pasan por el despachador.
    avisos = [a for a in notificaciones.avisos[avisos_previos:] if isinstance(a, AvisoExcursion)]
    return primera["id"], avisos, _alertas(client, token_qf, estado="pendiente")


_SIN_CONTINUIDAD = {
    # Sonda 1 del verificador: dos lecturas sin temperatura interna entre la
    # atención y la excursión nueva (no hay ninguna lectura válida en rango).
    "falla_de_sensor": (9, [(2, None), (3, None), (7, 8.5)]),
    # Sonda 2: 80 minutos sin lecturas después de atenderla.
    "hueco_sin_lecturas": (90, [(80, 9.5)]),
}


@pytest.mark.parametrize("escenario", list(_SIN_CONTINUIDAD))
async def test_excursion_sin_continuidad_tras_atendida_abre_alerta_pendiente_nueva(
    client, token_farmaceutico, db_session_factory, ingesta, escenario
):
    """IA-D18 (P0): sin una lectura válida en rango entre medio, la excursión que
    llega después de una lectura no válida o de un hueco largo no se suma a la
    alerta ATENDIDA: esta se cierra (`sin_continuidad_tras_atencion`) y se abre
    una alerta PENDIENTE nueva con su aviso."""
    ingesta(_ServicioPreventivo())
    hace_min, pasos = _SIN_CONTINUIDAD[escenario]

    atendida_id, avisos, pendientes = await _atendida_y_despues(
        client, token_farmaceutico, db_session_factory, hace_min, pasos
    )

    assert len(pendientes) == 1, pendientes
    nueva = pendientes[0]
    assert nueva["id"] != atendida_id
    assert nueva["nivel_riesgo"] == NivelRiesgo.EXCURSION_CRITICA.value
    assert nueva["episodio_abierto"] is True
    assert [str(a.alerta_id) for a in avisos] == [nueva["id"]]
    atendida = next(a for a in _alertas(client, token_farmaceutico) if a["id"] == atendida_id)
    assert (atendida["estado"], atendida["episodio_abierto"]) == ("atendida", False)
    [cierre] = await _cerradas(db_session_factory)
    assert cierre["alerta_id"] == atendida_id
    # Valor en la cadena (no la constante): es parte del contrato del evento.
    assert cierre["motivo_cierre"] == "sin_continuidad_tras_atencion"


async def test_excursion_continua_tras_atendida_sigue_en_el_mismo_episodio(
    client, token_farmaceutico, db_session_factory, ingesta
):
    """La excursión que nunca se interrumpió (8,5 °C → atendida → 8,6 °C 30 s
    después) sigue siendo el mismo episodio, como hasta ahora: sin alerta nueva
    ni aviso, y el pico se actualiza."""
    ingesta(_ServicioPreventivo())

    atendida_id, avisos, pendientes = await _atendida_y_despues(
        client, token_farmaceutico, db_session_factory, 9, [(0.5, 8.6)]
    )

    assert pendientes == []
    assert avisos == []
    [atendida] = _alertas(client, token_farmaceutico)
    assert atendida["id"] == atendida_id
    assert (atendida["estado"], atendida["episodio_abierto"]) == ("atendida", True)
    assert atendida["temperatura_pico"] == 8.6
    assert await _cerradas(db_session_factory) == []


class _ServicioConmutable(_ServicioPreventivo):
    """Modelo activo cuya respuesta en rango cambia durante la prueba."""

    def __init__(self) -> None:
        super().__init__(CALIDAD_OK, None, clase=NivelRiesgo.RIESGO_PREVENTIVO)

    def preventivo(self) -> None:
        self._estado, self._motivo, self._clase = CALIDAD_OK, None, NivelRiesgo.RIESGO_PREVENTIVO

    def normal(self) -> None:
        self._estado, self._motivo, self._clase = CALIDAD_OK, None, NivelRiesgo.NORMAL

    def ciego(self) -> None:
        self._estado, self._motivo, self._clase = (
            CALIDAD_HISTORIAL_INSUFICIENTE, "ventana_15min_sin_cobertura_suficiente", None
        )

    def sin_modelo(self) -> None:
        """Versión desactivada o artefacto rechazado: no hay modelo cargado."""
        self._estado, self._motivo, self._clase = CALIDAD_MODELO_NO_DISPONIBLE, "modelo_no_cargado", None


async def _preventiva(client, token, factory, servicio: _ServicioConmutable, t0: datetime, pasos) -> dict:
    """Abre una alerta preventiva a 7,5 °C en `t0` y aplica `pasos`: (minuto,
    respuesta del modelo, temperatura). Devuelve la alerta resultante."""
    async with factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    servicio.preventivo()
    await _mqtt(_Notificaciones(), t0, 7.5)
    [alerta] = _alertas(client, token)
    assert (alerta["nivel_riesgo"], alerta["episodio_abierto"]) == ("riesgo_preventivo", True)
    return await _seguir(client, token, servicio, t0, pasos)


async def _seguir(client, token, servicio: _ServicioConmutable, t0: datetime, pasos) -> dict:
    for minuto, respuesta, temperatura in pasos:
        getattr(servicio, respuesta)()
        await _mqtt(_Notificaciones(), t0 + timedelta(minutes=minuto), temperatura)
    [alerta] = _alertas(client, token)
    return alerta


def _t0_preventiva() -> datetime:
    return datetime.now(tz=timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=40)


def _minuto(t0: datetime, minuto: int) -> str:
    return (t0 + timedelta(minutes=minuto)).strftime("%Y-%m-%dT%H:%M")


async def test_la_preventiva_no_se_resuelve_mientras_el_modelo_no_ve(
    client, token_farmaceutico, db_session_factory, ingesta
):
    """HU-20 esc. 3 / HU-34 esc. 4: 12 minutos con HISTORIAL_INSUFICIENTE (p. ej.
    tras reiniciar el nodo) no son una ventana en Normal: la alerta preventiva
    sigue abierta. Después, 10 minutos de «normal» del modelo la resuelven."""
    servicio, t0 = _ServicioConmutable(), _t0_preventiva()
    ingesta(servicio)

    ciegos = [(m, "ciego", 7.5) for m in range(1, 13)]
    alerta = await _preventiva(client, token_farmaceutico, db_session_factory, servicio, t0, ciegos)

    assert alerta["episodio_abierto"] is True
    assert alerta["normalizacion_desde"] is None
    assert await _cerradas(db_session_factory) == []

    alerta = await _seguir(client, token_farmaceutico, servicio, t0, [(m, "normal", 5.0) for m in range(13, 24)])

    assert alerta["episodio_abierto"] is False
    [cierre] = await _cerradas(db_session_factory)
    assert cierre["motivo_cierre"] == CIERRE_NORMALIZACION
    assert cierre["normalizacion_desde"].startswith(_minuto(t0, 13))


async def test_una_lectura_sin_clase_reinicia_la_ventana_preventiva(
    client, token_farmaceutico, db_session_factory, ingesta
):
    """Normal del modelo → sin clase → normal: la ventana vuelve a empezar en la
    primera lectura Normal posterior; el tramo sin clase no cuenta."""
    servicio, t0 = _ServicioConmutable(), _t0_preventiva()
    ingesta(servicio)

    pasos = [(1, "normal", 5.0), (2, "ciego", 5.0), (5, "ciego", 5.0), (11, "normal", 5.0)]
    alerta = await _preventiva(client, token_farmaceutico, db_session_factory, servicio, t0, pasos)

    assert alerta["episodio_abierto"] is True  # 10 min desde el minuto 1, pero no seguidos
    assert alerta["normalizacion_desde"].startswith(_minuto(t0, 11))

    alerta = await _seguir(client, token_farmaceutico, servicio, t0, [(21, "normal", 5.0)])

    assert alerta["episodio_abierto"] is False
    [cierre] = await _cerradas(db_session_factory)
    assert cierre["motivo_cierre"] == CIERRE_NORMALIZACION
    assert cierre["normalizacion_desde"].startswith(_minuto(t0, 11))


@pytest.mark.parametrize("legado", ["desactivado", "experimental"])
async def test_sin_modelo_vuelve_la_linea_base_y_la_preventiva_se_resuelve_con_la_regla(
    client, token_farmaceutico, db_session_factory, ingesta, legado
):
    """Vuelta atrás (P-12, R-A con IA_LEGADO_MODO=desactivado, o legado rechazado):
    sin un modelo que decida, la regla 2-8 °C vuelve a mandar como antes de la
    activación, y la alerta preventiva abierta se resuelve tras la ventana. Si
    se exigiera la clase de un modelo que ya no está, no se cerraría nunca."""
    servicio, t0 = _ServicioConmutable(), _t0_preventiva()
    ingesta(servicio, legado)

    sin_modelo = [(m, "sin_modelo", 5.0) for m in range(1, 12)]
    alerta = await _preventiva(client, token_farmaceutico, db_session_factory, servicio, t0, sin_modelo)

    assert alerta["episodio_abierto"] is False
    [cierre] = await _cerradas(db_session_factory)
    assert cierre["motivo_cierre"] == CIERRE_NORMALIZACION
    assert cierre["normalizacion_desde"].startswith(_minuto(t0, 1))
