"""Ronda 3, integración de pistas: un modelo `termico_ambiente` (SHT31) va de
la CLI de registro (HU-18 P-2, pista «registro») al backend en sombra y a
/activar, pasando por la comprobación de conjunto servible (HU-17 P0-4, pista
«features»).

Cada pista lo probaba por su lado: el registro con un modelo `termico_minimo`
(y `termico_ambiente` solo llamando a `predecir` a mano), y HU-17 con un
manifiesto escrito con `registro.registrar`, sin la CLI. Ninguna prueba unía
las dos: con solo la pista de registro la CLI rechaza `termico_ambiente` (el
servicio no recibía el SHT31 y el modelo nunca clasificaría), y con solo la
de features no existe `corridas --registrar`. Aquí, sobre la integración:

1. la CLI registra `termico_ambiente` y su manifiesto es servible
   (`motivo_esquema_no_servible` → None; 32 features del extractor);
2. con IA_PREVENTIVO_MODO=sombra el backend lo carga y, por la ingesta MQTT
   real, deja predicciones `ok` mientras llega el SHT31 y, cuando falta,
   HISTORIAL_INSUFICIENTE con el motivo del contrato entre pistas
   `ventana_15min_sin_contexto_ambiental`; en sombra no decide nada: la regla
   2-8 °C deja normal lo que está en rango y la excursión abre su alerta;
3. aprobado con la CLI y en modo activo, POST /activar responde 200 (ni
   `esquema_features_no_servible` ni ningún otro 409 del manifiesto).

Los datos son SINTÉTICOS (tests/corridas_sinteticas.py): comprueban el camino,
no la capacidad del modelo."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva import servicio as modulo_servicio
from src.infrastructure.ai.prediccion_preventiva.__main__ import main as cli
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_EXCURSION_EN_CURSO,
    CALIDAD_HISTORIAL_INSUFICIENTE,
    CALIDAD_OK,
    ESTADO_APROBADO,
    ESTADO_EXPERIMENTAL,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import CONJUNTO_TERMICO_AMBIENTE, nombres_features
from src.infrastructure.ai.prediccion_preventiva.servicio import (
    ACTIVACION_ESQUEMA_NO_SERVIBLE,
    MODO_ACTIVO,
    MODO_SOMBRA,
    ServicioPrediccionPreventiva,
    motivo_esquema_no_servible,
)
from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.interface import main
from tests.conftest import auth_header
from tests.corridas_sinteticas import escribir_corridas

VERSION = "rf-corridas-h30-ambiente-0.1.0"
DEVICE_ID = "FARM-R3-AMBIENTE-01"
TOPIC = f"farmacias/{DEVICE_ID}/lecturas"
# Contrato entre pistas (HU-17): motivo de omisión cuando falta el SHT31.
MOTIVO_SIN_CONTEXTO_AMBIENTAL = "ventana_15min_sin_contexto_ambiental"
_VARIABLES = ("IA_PREVENTIVO_MODO", "IA_PREVENTIVO_VERSION", "IA_PREVENTIVO_SHA256", "IA_LEGADO_MODO")
# Con 2 incidentes sintéticos no se alcanzan los criterios reales; para ejercer
# /activar se registran métricas que los cumplen (como en la prueba e2e de P-2).
_METRICAS_QUE_CUMPLEN = {
    "f1_macro": 0.88, "incidentes_evaluables": 12, "sensibilidad_incidente": 0.92,
    "ic95_inferior_sensibilidad": 0.62, "falsas_alarmas_por_dia_sensor": 1.1,
    "mediana_anticipacion_min": 9.0, "ap_random_forest": 0.71, "ap_mejor_baseline": 0.55,
}


class _Mensaje:
    def __init__(self, payload: str) -> None:
        self.topic = TOPIC
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

    async def detener(self, timeout: float = 5.0) -> None:
        return None


@pytest.fixture
def registrada(tmp_path, capsys):
    """Registra VERSION (`termico_ambiente`) con la CLI desde el CSV sintético de
    5 corridas con SHT31 (prueba C4 y C5)."""
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=5)
    raiz = tmp_path / "registro"
    codigo = cli([
        "corridas", "--csv", str(csv), "--prueba", "C4,C5", "--horizonte", "30",
        "--registrar", "--version", VERSION, "--conjunto", CONJUNTO_TERMICO_AMBIENTE,
        "--directorio-registro", str(raiz),
    ])
    salida = capsys.readouterr()
    assert codigo == 0, salida.err
    return {"raiz": raiz, "resumen": json.loads(salida.out)}


@pytest.fixture
def entorno_ia(db_session_factory, monkeypatch):
    """Fija el entorno IA, la sesión de la ingesta MQTT y la instancia del
    servicio preventivo (con el registro temporal); los restaura al final."""
    anteriores = {k: os.environ.get(k) for k in _VARIABLES}
    monkeypatch.setattr(main, "_session_factory", db_session_factory)

    def _fijar(modo: str, sha: str, raiz) -> ServicioPrediccionPreventiva:
        os.environ.update(
            IA_PREVENTIVO_MODO=modo, IA_PREVENTIVO_VERSION=VERSION, IA_PREVENTIVO_SHA256=sha,
            IA_LEGADO_MODO="experimental",
        )
        get_settings.cache_clear()
        servicio = ServicioPrediccionPreventiva(modo, VERSION, sha, raiz=raiz)
        # obtener_servicio_preventivo (ingesta y API) reutiliza esta instancia:
        # mismo modo, versión y SHA-256 que el entorno.
        monkeypatch.setattr(modulo_servicio, "_instancia", servicio)
        monkeypatch.setattr(rf, "_instancia", rf.RandomForestRiesgoService())
        return servicio

    try:
        yield _fijar
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        get_settings.cache_clear()


_SEQ = iter(range(1, 100_000))


async def _mqtt(notificaciones, instante: datetime, temperatura: float, *, con_sht31: bool = True) -> None:
    datos = {
        "device_id": DEVICE_ID, "timestamp": instante.isoformat(), "schema_version": 1,
        "reading_id": f"r3-ambiente-{next(_SEQ)}", "temperatura_interna": temperatura,
        "temperatura_ambiental": 22.0 if con_sht31 else None, "humedad_ambiental": 55.0 if con_sht31 else None,
        "apertura_refrigerador": False, "estado_conectividad": "online", "time_quality": "synced",
    }
    await main._procesar_lectura_mqtt(_Mensaje(json.dumps(datos)), _BroadcasterMudo(), None, notificaciones)


def test_la_cli_registra_termico_ambiente_con_un_manifiesto_que_el_servicio_puede_servir(registrada):
    raiz, resumen = registrada["raiz"], registrada["resumen"]
    manifiesto = registro.leer_manifiesto(VERSION, raiz)

    assert manifiesto["conjunto_features"] == CONJUNTO_TERMICO_AMBIENTE
    assert manifiesto["features"] == nombres_features(ConfiguracionPreventiva(), CONJUNTO_TERMICO_AMBIENTE)
    assert {"temp_amb_actual", "humedad_actual"} <= set(manifiesto["features"])
    # La misma comprobación que hacen la carga y /activar (HU-17).
    assert motivo_esquema_no_servible(manifiesto) is None
    # La CLI verificó la carga en sombra y la paridad con el vector del entrenamiento.
    assert resumen["verificacion"]["carga_en_sombra"] is True
    assert resumen["paridad_servicio"]["comprobada"] is True and resumen["paridad_servicio"]["features"] == 32
    assert resumen["cuerpo_registro_api"]["feature_schema_version"] == CONJUNTO_TERMICO_AMBIENTE


async def test_en_sombra_la_ingesta_registra_predicciones_con_sht31_y_sin_el_el_motivo_del_contrato(
    client, token_admin, token_farmaceutico, db_session_factory, registrada, entorno_ia
):
    sha = registrada["resumen"]["sha256"]
    entorno_ia(MODO_SOMBRA, sha, registrada["raiz"])

    estado = client.get("/api/ia/preventivo", headers=auth_header(token_admin))
    assert estado.status_code == 200, estado.text
    cuerpo = estado.json()
    assert (cuerpo["modo"], cuerpo["disponible"], cuerpo["motivo_codigo"]) == ("sombra", True, None)
    assert (cuerpo["version"], cuerpo["estado_modelo"], cuerpo["sha256"]) == (VERSION, ESTADO_EXPERIMENTAL, sha)
    assert cuerpo["conjunto_features"] == CONJUNTO_TERMICO_AMBIENTE

    async with db_session_factory() as session:
        await SQLAlchemyDeviceRepository(session).obtener_o_crear(DEVICE_ID)
        await session.commit()
    notificaciones = _Notificaciones()
    inicio = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=22)
    for minuto in range(18):  # 18 min a 5 °C con el SHT31: cubre la ventana de 15 min
        await _mqtt(notificaciones, inicio + timedelta(minutes=minuto), 5.0)
    for minuto in (18, 19, 20):  # el SHT31 deja de enviar dato
        await _mqtt(notificaciones, inicio + timedelta(minutes=minuto), 5.0, con_sht31=False)
    await _mqtt(notificaciones, inicio + timedelta(minutes=21), 8.5, con_sht31=False)

    respuesta = client.get(
        "/api/ia/preventivo/predicciones", params={"device_id": DEVICE_ID}, headers=auth_header(token_farmaceutico)
    )
    assert respuesta.status_code == 200, respuesta.text
    predicciones = sorted(respuesta.json(), key=lambda p: p["timestamp_inferencia"])
    assert len(predicciones) == 22
    assert {p["modo"] for p in predicciones} == {"sombra"}
    assert {p["modelo_version"] for p in predicciones} == {VERSION}
    # Con el SHT31 y la ventana cubierta, el modelo clasifica (en sombra).
    con_sht31 = predicciones[17]
    assert (con_sht31["estado"], con_sht31["motivo"]) == (CALIDAD_OK, None)
    assert con_sht31["clase"] in (NivelRiesgo.NORMAL.value, NivelRiesgo.RIESGO_PREVENTIVO.value)
    # La primera sin SHT31 aún tiene, en sus últimos 90 s, la lectura anterior con SHT31.
    assert predicciones[18]["estado"] == CALIDAD_OK
    for sin_sht31 in predicciones[19:21]:
        assert (sin_sht31["estado"], sin_sht31["motivo"], sin_sht31["clase"]) == (
            CALIDAD_HISTORIAL_INSUFICIENTE, MOTIVO_SIN_CONTEXTO_AMBIENTAL, None,
        )
    assert predicciones[21]["estado"] == CALIDAD_EXCURSION_EN_CURSO

    # En sombra el preventivo no decide: la regla 2-8 °C (D18) deja normal lo que
    # está en rango y la excursión abre su alerta crítica con su aviso.
    async with db_session_factory() as session:
        lecturas = sorted(
            await SQLAlchemyLecturaRepository(session).listar_recientes_por_device(DEVICE_ID, limite=50),
            key=lambda x: x.timestamp,
        )
    assert len(lecturas) == 22
    assert {x.riesgo_efectivo for x in lecturas[:21]} == {NivelRiesgo.NORMAL}
    assert all(x.modelo_version != VERSION for x in lecturas)
    assert lecturas[21].riesgo_efectivo == NivelRiesgo.EXCURSION_CRITICA
    alertas = client.get(
        "/api/alertas", params={"device_id": DEVICE_ID}, headers=auth_header(token_farmaceutico)
    ).json()
    assert [(a["estado"], a["nivel_riesgo"]) for a in alertas] == [
        ("pendiente", NivelRiesgo.EXCURSION_CRITICA.value),
    ]
    assert [str(a.alerta_id) for a in notificaciones.avisos] == [alertas[0]["id"]]


def test_aprobada_con_la_cli_activar_responde_200_sin_esquema_no_servible(
    client, token_admin, registrada, entorno_ia, tmp_path
):
    raiz, resumen = registrada["raiz"], registrada["resumen"]
    sha = resumen["sha256"]
    evidencia = tmp_path / "informe_experimento_d.json"
    evidencia.write_text("{}", encoding="utf-8")
    base = ["--version", VERSION, "--responsable", "QA", "--directorio-registro", str(raiz)]
    assert cli(["estado", "--nuevo", "VALIDATED_OFFLINE", "--motivo", "criterios revisados", *base]) == 0
    assert cli([
        "estado", "--nuevo", ESTADO_APROBADO, "--motivo", "Experimento D aprobado", "--evidencia", str(evidencia), *base,
    ]) == 0
    servicio = entorno_ia(MODO_ACTIVO, sha, raiz)
    cuerpo = resumen["cuerpo_registro_api"]
    motivos = servicio.motivos_activacion(
        cuerpo["version"], cuerpo["model_hash"], horizonte_min=cuerpo["horizonte_min"],
        scikit_learn_version=cuerpo["scikit_learn_version"], feature_schema_version=cuerpo["feature_schema_version"],
    )
    assert ACTIVACION_ESQUEMA_NO_SERVIBLE not in motivos and motivos == []

    alta = client.post(
        "/api/ia/modelo/versiones", json={**cuerpo, "metricas": _METRICAS_QUE_CUMPLEN}, headers=auth_header(token_admin)
    )
    assert alta.status_code == 201, alta.text
    respuesta = client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 200, respuesta.text
    estado = client.get("/api/ia/preventivo", headers=auth_header(token_admin)).json()
    assert (estado["disponible"], estado["estado_modelo"], estado["conjunto_features"]) == (
        True, ESTADO_APROBADO, CONJUNTO_TERMICO_AMBIENTE,
    )
    assert estado["es_version_activa_registrada"] is True
