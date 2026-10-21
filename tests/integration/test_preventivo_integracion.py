"""Integración del modelo preventivo con la ingesta de lecturas y la API."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sqlalchemy import select

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.predecir_riesgo_preventivo import PredecirRiesgoPreventivoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    ESTADO_APROBADO,
    ESTADO_VALIDADO_OFFLINE,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import nombres_features
from src.infrastructure.ai.prediccion_preventiva.servicio import (
    MODO_ACTIVO,
    MODO_SOMBRA,
    ServicioPrediccionPreventiva,
)
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.models import (
    PrediccionPreventivaModel,
    ThermalAlertModel,
    TraceabilityRecordModel,
)
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.prediccion_preventiva_repository import (
    SQLAlchemyPrediccionPreventivaRepository,
)
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from tests.conftest import auth_header

CONFIG = ConfiguracionPreventiva()
VERSION = "rf-integracion-1.0.0"
BASE = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(hours=3)


def _registrar_modelo(raiz, aprobado: bool):
    nombres = nombres_features(CONFIG)
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, (300, len(nombres)))
    j = nombres.index("pendiente_5m")
    x[:, j] = rng.uniform(-0.2, 0.2, 300)
    modelo = RandomForestClassifier(n_estimators=10, random_state=0).fit(x, (x[:, j] > 0.05).astype(int))
    registro.registrar(modelo, {
        "version": VERSION, "estado": "EXPERIMENTAL", "conjunto_features": "termico_minimo",
        "features": nombres, "configuracion": CONFIG.como_dict(), "umbral_decision": 0.5,
        "puntuacion_calibrada": False, "versiones": {"scikit_learn": sklearn.__version__},
    }, raiz)
    if aprobado:
        registro.cambiar_estado(VERSION, ESTADO_VALIDADO_OFFLINE, "prueba", "qa", None, raiz)
        evidencia = raiz / "exp_d.json"
        evidencia.write_text("{}", encoding="utf-8")
        registro.cambiar_estado(VERSION, ESTADO_APROBADO, "prueba", "qa", evidencia, raiz)


def _use_case(session, servicio=None):
    lecturas = SQLAlchemyLecturaRepository(session)
    return RegistrarLecturaTermicaUseCase(
        lecturas,
        SQLAlchemyAlertaRepository(session),
        SQLAlchemyTrazabilidadRepository(session),
        ClasificarRiesgoTermicoUseCase(get_random_forest_service()),
        device_repository=SQLAlchemyDeviceRepository(session),
        registro_dispositivos_estricto=False,
        prediccion_preventiva=(
            PredecirRiesgoPreventivoUseCase(servicio, lecturas, SQLAlchemyPrediccionPreventivaRepository(session))
            if servicio is not None
            else None
        ),
    )


def _lectura(device: str, t: datetime, temp: float, **extra) -> LecturaTermica:
    return LecturaTermica(
        device_id=device, timestamp=t, temperatura_ambiental=22.0, humedad_ambiental=50.0,
        temperatura_interna=temp, apertura_refrigerador=False, estado_conectividad="online",
        time_quality="synced", **extra,
    )


async def _ingestar_serie(factory, device: str, servicio, temps: list[float], paso_s: int = 60):
    resultados = []
    for i, temp in enumerate(temps):
        async with factory() as session:
            resultados.append(await _use_case(session, servicio).execute(_lectura(device, BASE + timedelta(seconds=paso_s * i), temp)))
            await session.commit()
    return resultados


async def _predicciones(factory, device):
    async with factory() as session:
        return (await session.execute(
            select(PrediccionPreventivaModel).where(PrediccionPreventivaModel.device_id == device)
            .order_by(PrediccionPreventivaModel.timestamp_inferencia)
        )).scalars().all()


async def test_modo_desactivado_conserva_el_flujo_anterior(db_session_factory):
    lecturas = await _ingestar_serie(db_session_factory, "ESP32-PREV-OFF", None, [5.0] * 5)
    assert all(lectura.origen_clasificacion is not None for lectura in lecturas[2:])
    assert await _predicciones(db_session_factory, "ESP32-PREV-OFF") == []


async def test_modo_sombra_registra_predicciones_sin_tocar_la_clasificacion(db_session_factory, tmp_path):
    _registrar_modelo(tmp_path, aprobado=False)
    servicio = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=tmp_path)
    temps = [5.0] * 20 + [5.0 + 0.12 * i for i in range(1, 6)]
    sombra = await _ingestar_serie(db_session_factory, "ESP32-PREV-SOMBRA", servicio, temps)
    legado = await _ingestar_serie(db_session_factory, "ESP32-PREV-LEG", None, temps)

    preds = await _predicciones(db_session_factory, "ESP32-PREV-SOMBRA")
    assert len(preds) == len(temps)
    assert preds[0].estado == "HISTORIAL_INSUFICIENTE"
    assert preds[-1].estado == "ok" and preds[-1].clase == NivelRiesgo.RIESGO_PREVENTIVO.value
    assert preds[-1].modelo_sha256 and preds[-1].lectura_id == sombra[-1].id
    # La clasificación de la lectura es idéntica a la del flujo sin modelo preventivo.
    for a, b in zip(sombra, legado, strict=True):
        assert (a.nivel_riesgo, a.origen_clasificacion, a.riesgo_efectivo) == (b.nivel_riesgo, b.origen_clasificacion, b.riesgo_efectivo)


async def test_modo_sombra_no_cambia_el_payload_de_trazabilidad(db_session_factory, tmp_path):
    _registrar_modelo(tmp_path, aprobado=False)
    servicio = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=tmp_path)
    await _ingestar_serie(db_session_factory, "ESP32-PREV-HASH", servicio, [5.0] * 3)
    async with db_session_factory() as session:
        eslabon = (await session.execute(
            select(TraceabilityRecordModel).where(TraceabilityRecordModel.device_id == "ESP32-PREV-HASH")
        )).scalars().first()
    assert not any("preventiv" in clave for clave in eslabon.payload)


async def test_modo_activo_decide_el_nivel_y_la_excursion_sigue_siendo_critica(db_session_factory, tmp_path):
    _registrar_modelo(tmp_path, aprobado=True)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, raiz=tmp_path)
    temps = [5.0] * 20 + [5.0 + 0.12 * i for i in range(1, 6)] + [9.5]
    lecturas = await _ingestar_serie(db_session_factory, "ESP32-PREV-ACT", servicio, temps)
    assert lecturas[0].origen_clasificacion == "dato_insuficiente"
    subida = lecturas[-2]
    assert subida.nivel_riesgo == NivelRiesgo.RIESGO_PREVENTIVO
    assert subida.modelo_version == VERSION
    assert subida.confianza_ia is None, "una puntuación no calibrada no se presenta como probabilidad"
    critica = lecturas[-1]
    assert critica.nivel_riesgo is None
    assert critica.riesgo_efectivo == NivelRiesgo.EXCURSION_CRITICA
    assert critica.excursion_confirmada


async def test_alerta_critica_no_depende_de_que_el_modelo_cargue(db_session_factory, tmp_path):
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, "version-inexistente", raiz=tmp_path)
    lecturas = await _ingestar_serie(db_session_factory, "ESP32-PREV-SINMOD", servicio, [5.0, 5.0, 9.8])
    assert lecturas[-1].riesgo_efectivo == NivelRiesgo.EXCURSION_CRITICA
    async with db_session_factory() as session:
        alertas = (await session.execute(
            select(ThermalAlertModel).where(ThermalAlertModel.device_id == "ESP32-PREV-SINMOD")
        )).scalars().all()
    assert any(a.nivel_riesgo == "excursion_critica" for a in alertas)
    # Sin modelo, en activo se conserva la clasificación existente (no se rompe la ingesta).
    assert lecturas[1].origen_clasificacion is not None


async def test_lecturas_simuladas_no_se_predicen(db_session_factory, tmp_path):
    _registrar_modelo(tmp_path, aprobado=False)
    servicio = ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, raiz=tmp_path)
    async with db_session_factory() as session:
        await _use_case(session, servicio).execute(
            _lectura("ESP32-PREV-SIM", BASE, 5.0, payload={"simulated": True})
        )
        await session.commit()
    assert await _predicciones(db_session_factory, "ESP32-PREV-SIM") == []


async def test_predicciones_son_de_solo_lectura_por_api(client, token_farmaceutico):
    respuesta = client.get("/api/ia/preventivo/predicciones", headers=auth_header(token_farmaceutico))
    assert respuesta.status_code == 200
    assert client.post("/api/ia/preventivo/predicciones", headers=auth_header(token_farmaceutico)).status_code == 405


async def test_estado_del_modelo_preventivo(client, token_farmaceutico, token_tecnico):
    assert client.get("/api/ia/preventivo").status_code == 401
    assert client.get("/api/ia/preventivo", headers=auth_header(token_tecnico)).status_code == 403
    cuerpo = client.get("/api/ia/preventivo", headers=auth_header(token_farmaceutico)).json()
    assert cuerpo["modo"] == "desactivado"
    assert cuerpo["disponible"] is False
    assert isinstance(cuerpo["versiones_registradas"], list)


async def test_modelo_legado_se_declara_entrenado_con_datos_simulados(client, token_farmaceutico):
    cuerpo = client.get("/api/ia/modelo", headers=auth_header(token_farmaceutico)).json()
    assert cuerpo["datos_simulados"] is True
    assert cuerpo["estado_evaluacion"] == "ilustrativa_no_reportable"


def test_produccion_activo_exige_version_y_hash():
    from src.infrastructure.config import Settings

    with pytest.raises(ValueError):
        Settings(
            environment="production", jwt_secret_key="k" * 40, trazabilidad_hmac_key="h" * 40,
            allowed_hosts=["api.x"], cors_origins=["https://x"], database_url="postgresql+asyncpg://a:b@h/d",
            mqtt_enabled=False, ia_preventivo_modo="activo", ia_preventivo_version="v",
        )
