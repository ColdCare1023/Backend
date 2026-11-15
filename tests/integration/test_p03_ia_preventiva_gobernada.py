"""P0-3 de la validación final (HU-16, HU-18, HU-20, HU-34, HU-46, HU-47).

Con IA_PREVENTIVO_MODO=activo el modelo que clasifica es el preventivo, así que:

- POST /activar valida el hash del artefacto preventivo que se cargará, no el
  del Random Forest legado (antes respondía 409 a la versión correcta y 200 a
  una versión con el hash del legado);
- el modelo solo carga si IA_PREVENTIVO_VERSION e IA_PREVENTIVO_SHA256 son los
  de la versión activa del registro; si no, /health/detalle dice por qué;
- cada activación encadena MODELO_IA_VERSION_ACTIVADA con el modelo y su hash;
- la evidencia HU-47 de una lectura clasificada por esa versión dice que era
  la activa y aprobada (política «aprobada»), también tras reactivarla.
"""

import os
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
from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva import servicio as modulo_servicio
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    ESTADO_APROBADO,
    ESTADO_VALIDADO_OFFLINE,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import nombres_features
from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_ACTIVO, ServicioPrediccionPreventiva
from src.infrastructure.ai.random_forest_service import RandomForestRiesgoService
from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel, TraceabilityRecordModel
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.prediccion_preventiva_repository import (
    SQLAlchemyPrediccionPreventivaRepository,
)
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.interface import main
from tests.conftest import auth_header

VERSION = "rf-preventivo-p03-1.0.0"
CONFIG = ConfiguracionPreventiva()
HASH_LEGADO = RandomForestRiesgoService().hash_artefacto()
VERSION_LEGADO = RandomForestRiesgoService().metadata["model_version"]
_METRICAS = {
    "f1_macro": 0.88, "incidentes_evaluables": 12, "sensibilidad_incidente": 0.92,
    "ic95_inferior_sensibilidad": 0.62, "falsas_alarmas_por_dia_sensor": 1.1,
    "mediana_anticipacion_min": 9.0, "ap_random_forest": 0.71, "ap_mejor_baseline": 0.55,
}


def _registrar_preventivo(raiz) -> str:
    """Modelo preventivo APPROVED en un registro temporal: riesgo si la
    pendiente de 5 min supera 0,05 °C/min."""
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
    registro.cambiar_estado(VERSION, ESTADO_VALIDADO_OFFLINE, "prueba", "qa", None, raiz)
    evidencia = raiz / "exp_d.json"
    evidencia.write_text("{}", encoding="utf-8")
    registro.cambiar_estado(VERSION, ESTADO_APROBADO, "prueba", "qa", evidencia, raiz)
    return registro.leer_manifiesto(VERSION, raiz)["artefacto"]["sha256"]


def _cuerpo_version(version: str, model_hash: str) -> dict:
    return {
        "version": version, "model_hash": model_hash, "feature_schema_version": "termico_minimo",
        "dataset_version": "corridas-2026-11", "scikit_learn_version": sklearn.__version__,
        "python_version": "3.12.13", "trained_at": "2026-11-01T00:00:00Z", "horizonte_min": 30,
        "metricas": _METRICAS, "particiones": {"entrenamiento": "C1,C2", "prueba": "C3", "separacion_min": 30},
    }


@pytest.fixture
def preventivo_activo(tmp_path, monkeypatch):
    """IA_PREVENTIVO_MODO=activo con la versión y el SHA-256 del modelo del
    registro temporal; la instancia del proceso lee ese registro."""
    sha = _registrar_preventivo(tmp_path)
    anteriores = {k: os.environ.get(k) for k in ("IA_PREVENTIVO_MODO", "IA_PREVENTIVO_VERSION", "IA_PREVENTIVO_SHA256")}
    os.environ.update(IA_PREVENTIVO_MODO="activo", IA_PREVENTIVO_VERSION=VERSION, IA_PREVENTIVO_SHA256=sha)
    get_settings.cache_clear()
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)
    monkeypatch.setattr(modulo_servicio, "_instancia", servicio)
    monkeypatch.setattr(rf, "_instancia", RandomForestRiesgoService())
    try:
        yield servicio, sha, tmp_path
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        get_settings.cache_clear()


@pytest.fixture
def legado_aislado(monkeypatch):
    monkeypatch.setattr(rf, "_instancia", RandomForestRiesgoService())
    monkeypatch.setattr(modulo_servicio, "_instancia", None)


async def _eslabones(factory, tipo: str) -> list[TraceabilityRecordModel]:
    async with factory() as session:
        filas = (await session.execute(select(TraceabilityRecordModel))).scalars().all()
    return [f for f in filas if f.tipo_evento == tipo]


# ── (a) /activar valida el artefacto que realmente se carga ─────────────────


def test_activar_la_version_preventiva_desplegada(client, token_admin, preventivo_activo):
    servicio, sha, _ = preventivo_activo
    assert client.post(
        "/api/ia/modelo/versiones", json=_cuerpo_version(VERSION, sha), headers=auth_header(token_admin)
    ).status_code == 201

    respuesta = client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["activa"] is True
    # Desde la activación se carga el preventivo y el legado no se rechaza.
    assert servicio.disponible is True and servicio.es_version_activa_registrada() is True
    assert rf.get_random_forest_service().modelo_disponible is True


async def test_activar_con_el_hash_del_legado_se_rechaza_si_clasifica_el_preventivo(
    client, token_admin, preventivo_activo, db_session_factory
):
    client.post(
        "/api/ia/modelo/versiones", json=_cuerpo_version("legado-renombrado-1.0.0", HASH_LEGADO),
        headers=auth_header(token_admin),
    )

    respuesta = client.post(
        "/api/ia/modelo/versiones/legado-renombrado-1.0.0/activar", headers=auth_header(token_admin)
    )

    assert respuesta.status_code == 409
    assert "artefacto desplegado" in respuesta.json()["detail"]
    async with db_session_factory() as session:
        filas = (await session.execute(select(AuditLogModel))).scalars().all()
    (rechazo,) = [f for f in filas if f.accion == "ACTIVAR_VERSION_MODELO_IA_RECHAZADO"]
    assert any("IA_PREVENTIVO_VERSION" in m for m in rechazo.detalle["motivos"])


# ── (c) cada activación queda encadenada con el modelo y su hash ────────────


async def test_la_activacion_encadena_el_modelo_y_su_hash(client, token_admin, preventivo_activo, db_session_factory):
    _, sha, _ = preventivo_activo
    client.post("/api/ia/modelo/versiones", json=_cuerpo_version(VERSION, sha), headers=auth_header(token_admin))
    client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))
    client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))

    eventos = await _eslabones(db_session_factory, "MODELO_IA_VERSION_ACTIVADA")
    assert len(eventos) == 2, "cada activación (también la repetida) queda encadenada"
    primero = min(eventos, key=lambda e: e.chain_seq)
    assert primero.payload["version_nueva"] == VERSION and primero.payload["version_anterior"] is None
    assert primero.payload["modelo"] == "preventivo"
    assert primero.payload["model_hash"] == sha
    assert primero.usuario_id is not None


def test_reactivar_la_version_activa_no_reinicia_su_periodo(client, token_admin, legado_aislado):
    client.post(
        "/api/ia/modelo/versiones", json=_cuerpo_version("legado-a-1.0.0", HASH_LEGADO),
        headers=auth_header(token_admin),
    )
    primera = client.post("/api/ia/modelo/versiones/legado-a-1.0.0/activar", headers=auth_header(token_admin)).json()
    segunda = client.post("/api/ia/modelo/versiones/legado-a-1.0.0/activar", headers=auth_header(token_admin)).json()

    assert segunda["activa"] is True
    assert segunda["activada_en"] == primera["activada_en"]


# ── (b) /health/detalle informa el modelo en uso y por qué no carga ─────────


def test_health_sin_version_activa_dice_por_que_no_carga(client, token_admin, preventivo_activo):
    respuesta = client.get("/health/detalle", headers=auth_header(token_admin))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    preventivo = cuerpo["modelo_ia"]["preventivo"]
    assert preventivo["disponible"] is False
    assert preventivo["motivo_codigo"] == "sin_version_activa_registrada"
    assert preventivo["motivo_rechazo"]
    assert cuerpo["modelo_ia"]["en_uso"] == {
        "modelo": "preventivo", "version": VERSION, "disponible": False, "aprobado": False,
    }
    assert cuerpo["status"] == "degradado"


def test_health_con_la_version_activa_dice_disponible_y_aprobado(client, token_admin, preventivo_activo):
    _, sha, _ = preventivo_activo
    client.post("/api/ia/modelo/versiones", json=_cuerpo_version(VERSION, sha), headers=auth_header(token_admin))
    client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))

    cuerpo = client.get("/health/detalle", headers=auth_header(token_admin)).json()

    modelo = cuerpo["modelo_ia"]
    assert modelo["preventivo"]["disponible"] is True and modelo["preventivo"]["motivo_codigo"] is None
    assert modelo["version_activa_registrada"] == VERSION
    assert modelo["en_uso"] == {"modelo": "preventivo", "version": VERSION, "disponible": True, "aprobado": True}
    # El legado sigue cargado como respaldo experimental, no rechazado.
    assert modelo["disponible"] is True and modelo["motivo_rechazo"] is None
    assert cuerpo["status"] == "ok"


def test_health_por_defecto_solo_decide_la_regla(client, token_admin, legado_aislado):
    """Producción hoy: legado experimental y preventivo desactivado."""
    modelo = client.get("/health/detalle", headers=auth_header(token_admin)).json()["modelo_ia"]

    assert modelo["en_uso"] == {"modelo": "regla_2_8", "version": None, "disponible": False, "aprobado": False}
    assert modelo["preventivo"]["motivo_codigo"] == "modo_desactivado"
    assert modelo["motivo_codigo"] is None


async def test_al_arrancar_se_aplica_la_version_activa_al_preventivo(
    client, token_admin, preventivo_activo, db_session_factory, monkeypatch
):
    _, sha, raiz = preventivo_activo
    client.post("/api/ia/modelo/versiones", json=_cuerpo_version(VERSION, sha), headers=auth_header(token_admin))
    client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))
    # Reinicio del proceso: instancia nueva, sin la versión activa fijada.
    reiniciado = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=raiz)
    monkeypatch.setattr(modulo_servicio, "_instancia", reiniciado)
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    assert reiniciado.disponible is False

    await main._aplicar_version_activa()

    assert reiniciado.disponible is True


async def test_al_arrancar_sin_version_activa_queda_auditado_con_su_codigo(
    preventivo_activo, db_session_factory, monkeypatch
):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    await main._aplicar_version_activa()
    await main._auditar_modelos_rechazados(get_settings())

    async with db_session_factory() as session:
        filas = (await session.execute(select(AuditLogModel))).scalars().all()
    (fila,) = [f for f in filas if f.accion == "MODELO_RECHAZADO"]
    assert fila.detalle["modelo"] == "preventivo"
    assert fila.detalle["motivo_codigo"] == "sin_version_activa_registrada"


# ── (d) HU-47 esc. 2: la evidencia dice «aprobada» ──────────────────────────


async def _ingestar(factory, servicio, device: str, temps: list[float]) -> list[LecturaTermica]:
    base = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(hours=2)
    lecturas = []
    for i, temp in enumerate(temps):
        async with factory() as session:
            repo = SQLAlchemyLecturaRepository(session)
            lecturas.append(await RegistrarLecturaTermicaUseCase(
                repo, SQLAlchemyAlertaRepository(session), SQLAlchemyTrazabilidadRepository(session),
                ClasificarRiesgoTermicoUseCase(rf.get_random_forest_service(), "experimental"),
                device_repository=SQLAlchemyDeviceRepository(session), registro_dispositivos_estricto=False,
                ia_legado_modo="experimental",
                prediccion_preventiva=PredecirRiesgoPreventivoUseCase(
                    servicio, repo, SQLAlchemyPrediccionPreventivaRepository(session)
                ),
            ).execute(LecturaTermica(
                device_id=device, timestamp=base + timedelta(minutes=i), temperatura_ambiental=22.0,
                humedad_ambiental=50.0, temperatura_interna=temp, apertura_refrigerador=False,
                estado_conectividad="online", time_quality="synced",
            )))
            await session.commit()
    return lecturas


async def test_la_evidencia_de_la_version_preventiva_activa_dice_aprobada(
    client, token_admin, token_farmaceutico, preventivo_activo, db_session_factory
):
    servicio, sha, _ = preventivo_activo
    client.post("/api/ia/modelo/versiones", json=_cuerpo_version(VERSION, sha), headers=auth_header(token_admin))
    assert client.post(
        f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin)
    ).status_code == 200

    lecturas = await _ingestar(
        db_session_factory, servicio, "ESP32-P03-HU47", [5.0] * 20 + [5.0 + 0.12 * i for i in range(1, 6)]
    )
    subida = lecturas[-1]
    assert subida.modelo_version == VERSION
    # HU-34 esc. 2 / HU-20: con el modelo aprobado, el riesgo efectivo es preventivo.
    assert subida.riesgo_efectivo == NivelRiesgo.RIESGO_PREVENTIVO

    cuerpo = client.get(f"/api/ia/inferencia/{subida.id}", headers=auth_header(token_farmaceutico)).json()
    assert cuerpo["version_era_activa_y_aprobada_en_ese_momento"] is True
    assert cuerpo["version_activa_en_ese_momento"] == VERSION
    assert cuerpo["politica_operativa"] == "aprobada"
    assert cuerpo["ia_experimental"] is False
    assert cuerpo["horizonte_min"] == 30 and cuerpo["feature_schema_version"] == "termico_minimo"
    # HU-18 esc. 1 / HU-47 esc. 1: probabilidades de ambas clases, aunque el
    # manifiesto no las declare calibradas (y la evidencia lo dice).
    probabilidades = cuerpo["probabilidades_por_clase"]
    assert set(probabilidades) == {"normal", "riesgo_preventivo"}
    assert abs(sum(probabilidades.values()) - 1) < 1e-6
    assert cuerpo["probabilidades_calibradas"] is False
    clase = cuerpo["clasificacion_final"]
    assert cuerpo["confianza"] == pytest.approx(probabilidades[clase], abs=1e-4)
    assert cuerpo["timestamp_inferencia"] is not None and cuerpo["vector_features"]


def _tres_lecturas(client, token, device: str) -> dict:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=20)
    ultima = None
    for minutos in (0, 5, 10):
        ultima = client.post("/api/lecturas", json={
            "device_id": device, "timestamp": (base + timedelta(minutes=minutos)).isoformat(),
            "temperatura_interna": 5.0, "temperatura_ambiental": 21.0, "humedad_ambiental": 55.0,
            "apertura_refrigerador": False, "estado_conectividad": "online",
        }, headers=auth_header(token)).json()
    return ultima


def test_reactivar_una_version_conserva_la_evidencia_de_sus_periodos(
    client, token_admin, token_tecnico, token_farmaceutico, legado_aislado
):
    """X activa → lectura → Y activa → X otra vez → lectura: las dos lecturas
    clasificadas por X cruzan con X como activa y aprobada en su momento."""
    for version in (VERSION_LEGADO, "legado-y-1.0.0"):
        client.post("/api/ia/modelo/versiones", json=_cuerpo_version(version, HASH_LEGADO), headers=auth_header(token_admin))
    client.post(f"/api/ia/modelo/versiones/{VERSION_LEGADO}/activar", headers=auth_header(token_admin))
    primera = _tres_lecturas(client, token_tecnico, "FARM-P03-REACT-1")
    client.post("/api/ia/modelo/versiones/legado-y-1.0.0/activar", headers=auth_header(token_admin))
    client.post(f"/api/ia/modelo/versiones/{VERSION_LEGADO}/activar", headers=auth_header(token_admin))
    segunda = _tres_lecturas(client, token_tecnico, "FARM-P03-REACT-2")

    for lectura in (primera, segunda):
        assert lectura["estado_inferencia"] == "completada"
        cuerpo = client.get(f"/api/ia/inferencia/{lectura['id']}", headers=auth_header(token_farmaceutico)).json()
        assert cuerpo["version_activa_en_ese_momento"] == VERSION_LEGADO
        assert cuerpo["version_era_activa_y_aprobada_en_ese_momento"] is True


# ── HU-46/HU-16: un legado sin versión aprobada no decide ───────────────────


class _LegadoQueSiempreAnticipa(RandomForestRiesgoService):
    """El legado real (mismo artefacto y hash) cuya inferencia siempre dice
    riesgo_preventivo: así se ve si su clase decide o solo queda de evidencia."""

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


@pytest.fixture
def legado_en_modo_activo(monkeypatch):
    anterior = os.environ.get("IA_LEGADO_MODO")
    os.environ["IA_LEGADO_MODO"] = "activo"
    get_settings.cache_clear()
    monkeypatch.setattr(rf, "_instancia", _LegadoQueSiempreAnticipa())
    monkeypatch.setattr(modulo_servicio, "_instancia", None)
    try:
        yield
    finally:
        if anterior is None:
            os.environ.pop("IA_LEGADO_MODO", None)
        else:
            os.environ["IA_LEGADO_MODO"] = anterior
        get_settings.cache_clear()


def _preventivas(client, token, device: str) -> list[dict]:
    alertas = client.get("/api/alertas", params={"device_id": device}, headers=auth_header(token)).json()
    return [a for a in alertas if a["nivel_riesgo"] == "riesgo_preventivo"]


def test_legado_activo_sin_version_registrada_no_decide_y_con_ella_si(
    client, token_admin, token_tecnico, token_farmaceutico, legado_en_modo_activo
):
    """IA_LEGADO_MODO=activo sin versión activa registrada: el legado no está
    aprobado, así que su clase es evidencia experimental y no abre alertas
    preventivas. Tras registrar y activar su hash, sí decide."""
    sin_aprobar = _tres_lecturas(client, token_tecnico, "FARM-P03-LEG-1")
    assert sin_aprobar["ia_experimental"] is True
    assert sin_aprobar["riesgo_efectivo"] == "normal"
    assert _preventivas(client, token_farmaceutico, "FARM-P03-LEG-1") == []
    modelo = client.get("/health/detalle", headers=auth_header(token_admin)).json()["modelo_ia"]
    assert (modelo["modo_legado"], modelo["modo_legado_efectivo"]) == ("activo", "experimental")
    assert modelo["en_uso"]["modelo"] == "regla_2_8"

    client.post("/api/ia/modelo/versiones", json=_cuerpo_version(VERSION_LEGADO, HASH_LEGADO), headers=auth_header(token_admin))
    assert client.post(
        f"/api/ia/modelo/versiones/{VERSION_LEGADO}/activar", headers=auth_header(token_admin)
    ).status_code == 200

    aprobada = _tres_lecturas(client, token_tecnico, "FARM-P03-LEG-2")
    assert aprobada["ia_experimental"] is False
    assert aprobada["riesgo_efectivo"] == "riesgo_preventivo"
    assert len(_preventivas(client, token_farmaceutico, "FARM-P03-LEG-2")) == 1
    modelo = client.get("/health/detalle", headers=auth_header(token_admin)).json()["modelo_ia"]
    assert modelo["modo_legado_efectivo"] == "activo"
    assert modelo["en_uso"]["modelo"] == "legado" and modelo["en_uso"]["aprobado"] is True


def test_legado_activo_con_el_preventivo_gobernando_queda_experimental(preventivo_activo):
    from src.infrastructure.ai import gobierno

    class _Ajustes:
        ia_legado_modo = "activo"
        ia_preventivo_modo = "activo"

    assert gobierno.modo_legado_efectivo(_Ajustes()) == "experimental"
    _Ajustes.ia_legado_modo = "desactivado"
    assert gobierno.modo_legado_efectivo(_Ajustes()) == "desactivado"


# ── HU-46 esc. 3: el rechazo del preventivo activo es un error crítico ──────


def test_hash_distinto_del_registrado_es_error_critico_en_modo_activo(tmp_path, caplog, monkeypatch):
    import logging

    # alembic.fileConfig (test_migraciones) deshabilita los loggers existentes.
    monkeypatch.setattr(modulo_servicio.logger, "disabled", False)
    sha = _registrar_preventivo(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path, version_activa=(VERSION, "f" * 64))
    with caplog.at_level(logging.DEBUG):
        assert servicio.disponible is False

    assert servicio.motivo_codigo() == "hash_distinto_del_registrado"
    niveles = {r.levelname for r in caplog.records if "preventivo" in r.getMessage().lower()}
    assert "CRITICAL" in niveles


def test_en_sombra_el_preventivo_no_disponible_es_solo_un_aviso(tmp_path, caplog, monkeypatch):
    import logging

    monkeypatch.setattr(modulo_servicio.logger, "disabled", False)
    servicio = ServicioPrediccionPreventiva("sombra", "version-inexistente", raiz=tmp_path)
    with caplog.at_level(logging.DEBUG):
        assert servicio.disponible is False

    niveles = {r.levelname for r in caplog.records if "preventivo" in r.getMessage().lower()}
    assert niveles == {"WARNING"}


# ── HU-46 esc. 1: registrar y marcar como activa en una sola operación ──────


async def test_registrar_con_activar_la_deja_activa_y_auditada(client, token_admin, legado_aislado, db_session_factory):
    cuerpo = {**_cuerpo_version(VERSION_LEGADO, HASH_LEGADO), "activar": True}
    respuesta = client.post("/api/ia/modelo/versiones", json=cuerpo, headers=auth_header(token_admin))

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["activa"] is True and respuesta.json()["activada_en"] is not None
    versiones = client.get("/api/ia/modelo/versiones", headers=auth_header(token_admin)).json()
    assert [(v["version"], v["activa"]) for v in versiones] == [(VERSION_LEGADO, True)]
    async with db_session_factory() as session:
        acciones = [a.accion for a in (await session.execute(select(AuditLogModel))).scalars().all()]
    assert {"REGISTRAR_VERSION_MODELO_IA", "ACTIVAR_VERSION_MODELO_IA"} <= set(acciones)
    (cambio,) = await _eslabones(db_session_factory, "MODELO_IA_VERSION_ACTIVADA")
    assert cambio.payload["version_nueva"] == VERSION_LEGADO and cambio.payload["version_anterior"] is None


async def test_registrar_con_activar_rechazada_no_deja_nada_a_medias(
    client, token_admin, legado_aislado, db_session_factory
):
    client.post("/api/ia/modelo/versiones", json={**_cuerpo_version(VERSION_LEGADO, HASH_LEGADO), "activar": True},
                headers=auth_header(token_admin))
    otra = {**_cuerpo_version("legado-otro-artefacto", "e" * 64), "activar": True}

    respuesta = client.post("/api/ia/modelo/versiones", json=otra, headers=auth_header(token_admin))

    assert respuesta.status_code == 409
    versiones = client.get("/api/ia/modelo/versiones", headers=auth_header(token_admin)).json()
    # Ni registrada ni activa: la versión aprobada vigente sigue activa.
    assert [(v["version"], v["activa"]) for v in versiones] == [(VERSION_LEGADO, True)]
    async with db_session_factory() as session:
        rechazos = [
            a for a in (await session.execute(select(AuditLogModel))).scalars().all()
            if a.accion == "ACTIVAR_VERSION_MODELO_IA_RECHAZADO"
        ]
    assert len(rechazos) == 1 and rechazos[0].detalle["al_registrar"] is True
    assert rechazos[0].resultado == "fallido"


def test_registrar_sin_activar_conserva_el_comportamiento_anterior(client, token_admin, legado_aislado):
    respuesta = client.post(
        "/api/ia/modelo/versiones", json=_cuerpo_version(VERSION_LEGADO, HASH_LEGADO), headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 201 and respuesta.json()["activa"] is False
