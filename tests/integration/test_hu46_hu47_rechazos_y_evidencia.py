"""HU-46 esc. 2 y 3 y HU-47 esc. 2.

- Una activación rechazada (versión no registrada o que no cumple los
  criterios) deja su fila en la bitácora y NO toca la versión activa.
- Si el artefacto desplegado no coincide con el hash de la versión activa, el
  modelo no carga, se registra un error crítico (log CRITICAL y fila
  MODELO_RECHAZADO encadenada) y no se sirven clasificaciones.
- La evidencia de una inferencia dice si la versión que clasificó era la
  activa y aprobada en ese momento, y cuál era la activa.
"""

import logging
import shutil
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.random_forest_service import (
    DEFAULT_METADATA_PATH,
    DEFAULT_MODEL_PATH,
    MOTIVO_MODELO_RECHAZADO,
    RandomForestRiesgoService,
)
from src.infrastructure.database.models import AuditLogModel, TraceabilityRecordModel
from src.interface import main
from tests.conftest import auth_header

HASH_VALIDO = RandomForestRiesgoService().hash_artefacto()
VERSION_DESPLEGADA = RandomForestRiesgoService().metadata["model_version"]
DEVICE_ID = "FARM-HU47-02"

_METRICAS_QUE_CUMPLEN = {
    "f1_macro": 0.88, "incidentes_evaluables": 12, "sensibilidad_incidente": 0.92,
    "ic95_inferior_sensibilidad": 0.62, "falsas_alarmas_por_dia_sensor": 1.1,
    "mediana_anticipacion_min": 9.0, "ap_random_forest": 0.71, "ap_mejor_baseline": 0.55,
}


def _version(version: str, **overrides) -> dict:
    cuerpo = {
        "version": version,
        "model_hash": HASH_VALIDO,
        "feature_schema_version": "1",
        "dataset_version": "dataset-2026-08",
        "scikit_learn_version": "1.9.0",
        "python_version": "3.14.5",
        "trained_at": "2026-08-01T00:00:00Z",
        "horizonte_min": 30,
        "metricas": _METRICAS_QUE_CUMPLEN,
        "particiones": {"entrenamiento": "a", "prueba": "b", "separacion_min": 30},
    }
    cuerpo.update(overrides)
    return cuerpo


@pytest.fixture(autouse=True)
def _servicio_aislado(monkeypatch):
    """Cada prueba usa su propia instancia del servicio: activar una versión
    fija el hash aprobado y no debe filtrarse a otras pruebas."""
    monkeypatch.setattr(rf, "_instancia", RandomForestRiesgoService())


async def _auditoria(db_session_factory, accion: str) -> list[AuditLogModel]:
    async with db_session_factory() as session:
        filas = (await session.execute(select(AuditLogModel))).scalars().all()
    return [f for f in filas if f.accion == accion]


def _activa(client, token) -> str | None:
    versiones = client.get("/api/ia/modelo/versiones", headers=auth_header(token)).json()
    activas = [v["version"] for v in versiones if v["activa"]]
    return activas[0] if activas else None


# ── HU-46 esc. 2: rechazo auditado y versión activa intacta ─────────────────


async def test_activar_version_no_registrada_audita_y_conserva_la_activa(client, token_admin, db_session_factory):
    client.post("/api/ia/modelo/versiones", json=_version("1.0.0-ok"), headers=auth_header(token_admin))
    assert client.post("/api/ia/modelo/versiones/1.0.0-ok/activar", headers=auth_header(token_admin)).status_code == 200

    respuesta = client.post("/api/ia/modelo/versiones/9.9.9-fantasma/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 404
    assert _activa(client, token_admin) == "1.0.0-ok"
    (fila,) = await _auditoria(db_session_factory, "ACTIVAR_VERSION_MODELO_IA_RECHAZADO")
    assert fila.detalle == {"motivo": "version_no_registrada"}
    assert fila.recurso == "ia/modelo/versiones/9.9.9-fantasma/activar"
    assert fila.usuario_id is not None


async def test_activar_version_que_no_cumple_audita_los_motivos_y_conserva_la_activa(
    client, token_admin, db_session_factory
):
    client.post("/api/ia/modelo/versiones", json=_version("1.0.0-ok"), headers=auth_header(token_admin))
    client.post("/api/ia/modelo/versiones/1.0.0-ok/activar", headers=auth_header(token_admin))
    client.post(
        "/api/ia/modelo/versiones", json=_version("2.0.0-sin-h", horizonte_min=None), headers=auth_header(token_admin)
    )

    respuesta = client.post("/api/ia/modelo/versiones/2.0.0-sin-h/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 409
    assert _activa(client, token_admin) == "1.0.0-ok"
    (fila,) = await _auditoria(db_session_factory, "ACTIVAR_VERSION_MODELO_IA_RECHAZADO")
    assert fila.detalle["motivo"] == "criterios_no_cumplidos"
    assert any("horizonte" in m for m in fila.detalle["motivos"])


async def test_activar_version_con_otro_artefacto_se_rechaza_y_se_audita(client, token_admin, db_session_factory):
    client.post(
        "/api/ia/modelo/versiones", json=_version("3.0.0-otro", model_hash="c" * 64), headers=auth_header(token_admin)
    )

    respuesta = client.post("/api/ia/modelo/versiones/3.0.0-otro/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 409
    assert _activa(client, token_admin) is None
    (fila,) = await _auditoria(db_session_factory, "ACTIVAR_VERSION_MODELO_IA_RECHAZADO")
    assert any("artefacto desplegado" in m for m in fila.detalle["motivos"])


# ── HU-46 esc. 3: artefacto distinto del de la versión activa ───────────────


async def test_artefacto_cambiado_tras_activar_no_clasifica_y_deja_error_critico(
    client, token_admin, token_farmaceutico, db_session_factory, monkeypatch, tmp_path, caplog
):
    client.post("/api/ia/modelo/versiones", json=_version("1.0.0-ok"), headers=auth_header(token_admin))
    assert client.post("/api/ia/modelo/versiones/1.0.0-ok/activar", headers=auth_header(token_admin)).status_code == 200

    # Se reemplaza el archivo desplegado por otro (p. ej. una imagen nueva sin
    # registrar su versión). Su metadata es coherente, pero no es el aprobado.
    otro = tmp_path / "random_forest_termico.pkl"
    metadata = tmp_path / "model_metadata.json"
    shutil.copyfile(DEFAULT_MODEL_PATH, otro)
    with otro.open("ab") as archivo:
        archivo.write(b"otro-artefacto")
    import hashlib
    import json

    declarada = json.loads(DEFAULT_METADATA_PATH.read_text(encoding="utf-8"))
    declarada["model_hash"] = hashlib.sha256(otro.read_bytes()).hexdigest()
    metadata.write_text(json.dumps(declarada), encoding="utf-8")
    monkeypatch.setattr(rf, "_instancia", RandomForestRiesgoService(model_path=otro, metadata_path=metadata))
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    # alembic.fileConfig (test_migraciones) deshabilita los loggers existentes.
    monkeypatch.setattr(rf.logger, "disabled", False)

    with caplog.at_level(logging.CRITICAL, logger="infrastructure.ai.random_forest_service"):
        await main._aplicar_version_activa()
        await main._auditar_modelos_rechazados(main.get_settings())

    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    (fila,) = await _auditoria(db_session_factory, "MODELO_RECHAZADO")
    assert fila.detalle["modelo"] == "legado"
    assert "versión activa" in fila.detalle["motivo"]
    async with db_session_factory() as session:
        eslabones = (await session.execute(select(TraceabilityRecordModel))).scalars().all()
    assert any(e.tipo_evento == "AUDIT_LOG" and e.payload["accion"] == "MODELO_RECHAZADO" for e in eslabones)

    vector = {
        "temperatura_ambiental": 21.0, "humedad_ambiental": 55.0, "temperatura_interna": 5.0,
        "diferencia_sensores": 16.0, "duracion_fuera_rango": 0.0, "frecuencia_desviaciones": 0.0,
        "tendencia_termica": 0.0, "apertura_refrigerador": False, "hora_evento": 12,
        "estado_conectividad_online": True,
    }
    clasificacion = client.post("/api/ia/clasificar", json=vector, headers=auth_header(token_farmaceutico)).json()
    assert clasificacion["estado_inferencia"] == "modelo_no_disponible"
    assert clasificacion["confianza"] is None
    assert client.get("/api/ia/modelo", headers=auth_header(token_farmaceutico)).status_code == 503


# ── HU-47 esc. 2: coherencia con el versionado ──────────────────────────────


def _tres_lecturas(client, token) -> dict:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=20)
    ultima = None
    for minutos in (0, 5, 10):
        ultima = client.post(
            "/api/lecturas",
            json={
                "device_id": DEVICE_ID,
                "timestamp": (base + timedelta(minutes=minutos)).isoformat(),
                "temperatura_interna": 5.0,
                "temperatura_ambiental": 21.0,
                "humedad_ambiental": 55.0,
                "apertura_refrigerador": False,
                "estado_conectividad": "online",
            },
            headers=auth_header(token),
        ).json()
    return ultima


def test_inferencia_con_la_version_activa_y_aprobada(client, token_admin, token_tecnico, token_farmaceutico):
    client.post("/api/ia/modelo/versiones", json=_version(VERSION_DESPLEGADA), headers=auth_header(token_admin))
    client.post(f"/api/ia/modelo/versiones/{VERSION_DESPLEGADA}/activar", headers=auth_header(token_admin))

    lectura = _tres_lecturas(client, token_tecnico)
    assert lectura["estado_inferencia"] == "completada"
    cuerpo = client.get(f"/api/ia/inferencia/{lectura['id']}", headers=auth_header(token_farmaceutico)).json()

    assert cuerpo["modelo_version"] == VERSION_DESPLEGADA
    assert cuerpo["version_era_activa_y_aprobada_en_ese_momento"] is True
    assert cuerpo["version_activa_en_ese_momento"] == VERSION_DESPLEGADA
    assert cuerpo["horizonte_min"] == 30 and cuerpo["feature_schema_version"] == "1"


def test_inferencia_sin_version_aprobada_lo_dice(client, token_tecnico, token_farmaceutico):
    lectura = _tres_lecturas(client, token_tecnico)
    cuerpo = client.get(f"/api/ia/inferencia/{lectura['id']}", headers=auth_header(token_farmaceutico)).json()

    assert cuerpo["modelo_version"] == VERSION_DESPLEGADA
    assert cuerpo["version_era_activa_y_aprobada_en_ese_momento"] is False
    assert cuerpo["version_activa_en_ese_momento"] is None
    assert cuerpo["politica_operativa"] == "experimental"


def test_inferencia_con_otra_version_activa(client, token_admin, token_tecnico, token_farmaceutico):
    # La versión activa en la base es otra (mismo artefacto, otro nombre): la
    # clase no salió de la versión aprobada.
    client.post("/api/ia/modelo/versiones", json=_version("0.9.0-renombrada"), headers=auth_header(token_admin))
    client.post("/api/ia/modelo/versiones/0.9.0-renombrada/activar", headers=auth_header(token_admin))

    lectura = _tres_lecturas(client, token_tecnico)
    cuerpo = client.get(f"/api/ia/inferencia/{lectura['id']}", headers=auth_header(token_farmaceutico)).json()

    assert cuerpo["version_era_activa_y_aprobada_en_ese_momento"] is False
    assert cuerpo["version_activa_en_ese_momento"] == "0.9.0-renombrada"


def test_lectura_sin_inferencia_no_afirma_nada(client, token_tecnico, token_farmaceutico):
    primera = client.post(
        "/api/lecturas",
        json={
            "device_id": "FARM-HU47-SOLA",
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "temperatura_interna": 5.0, "temperatura_ambiental": 21.0, "humedad_ambiental": 55.0,
            "apertura_refrigerador": False, "estado_conectividad": "online",
        },
        headers=auth_header(token_tecnico),
    ).json()
    cuerpo = client.get(f"/api/ia/inferencia/{primera['id']}", headers=auth_header(token_farmaceutico)).json()

    assert primera["motivo_no_inferencia"] == "historial_insuficiente_para_tendencia"
    assert cuerpo["version_era_activa_y_aprobada_en_ese_momento"] is None
    assert cuerpo["version_activa_en_ese_momento"] is None
    assert cuerpo["probabilidades_por_clase"] is None


def test_rechazo_del_modelo_tambien_en_la_evidencia(client, token_tecnico, token_farmaceutico, monkeypatch):
    servicio = RandomForestRiesgoService()
    servicio.fijar_hash_aprobado("d" * 64)
    monkeypatch.setattr(rf, "_instancia", servicio)

    lectura = _tres_lecturas(client, token_tecnico)
    cuerpo = client.get(f"/api/ia/inferencia/{lectura['id']}", headers=auth_header(token_farmaceutico)).json()

    assert lectura["motivo_no_inferencia"] == MOTIVO_MODELO_RECHAZADO
    assert cuerpo["modelo_version"] is None
    assert cuerpo["version_era_activa_y_aprobada_en_ese_momento"] is None
