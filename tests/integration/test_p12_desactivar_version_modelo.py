"""P-12 (plan de pruebas de la IA preventiva): la vuelta atrás restaura la línea base.

Antes, tras «desactivar» el preventivo por entorno, V seguía activa en la base:
el arranque fijaba su hash al modelo legado, que quedaba rechazado
(`hash_distinto_de_la_version_activa`), y no había forma de dejar el registro
sin versión activa. Ahora:

    POST /api/ia/modelo/versiones/{version}/desactivar   {"motivo": "10..500 caracteres"}

- 200 con la versión ya inactiva; 404 si no está registrada; 409
  `version_no_activa` si no es la activa; 422 si el motivo no es válido; 403
  para cualquier rol que no sea administrador (como /activar);
- encadena MODELO_IA_VERSION_DESACTIVADA (quién, cuándo, versión, motivo y el
  hash anterior de la cadena) y deja DESACTIVAR_VERSION_MODELO_IA en la bitácora;
- no queda versión activa: el preventivo en modo activo no carga
  (`sin_version_activa_registrada`), el legado vuelve a verificarse con su
  metadata y el riesgo efectivo es el de antes de cualquier activación;
- HU-47: una lectura clasificada después no cruza con la versión desactivada.

También IA-B05 v6 y v7 por la API: el 409 de /activar trae `codigos`.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
import sklearn
from sqlalchemy import select

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.predecir_riesgo_preventivo import PredecirRiesgoPreventivoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.application.use_cases.verificar_integridad_registro import VerificarIntegridadRegistroUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva import servicio as modulo_servicio
from src.infrastructure.ai.prediccion_preventiva.configuracion import ESTADO_APROBADO
from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_ACTIVO, ServicioPrediccionPreventiva
from src.infrastructure.ai.random_forest_service import RandomForestRiesgoService
from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel, TraceabilityRecordModel, UserModel
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.modelo_ia_repository import SQLAlchemyModeloIARepository
from src.infrastructure.database.repositories.prediccion_preventiva_repository import (
    SQLAlchemyPrediccionPreventivaRepository,
)
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.interface import main
from tests.conftest import auth_header
from tests.ia_preventiva import registrar_preventivo

VERSION = "rf-preventivo-p12-1.0.0"
MOTIVO = "Rollback R-A: falsas alarmas sobre el umbral acordado con el QF"
_METRICAS = {
    "f1_macro": 0.88, "incidentes_evaluables": 12, "sensibilidad_incidente": 0.92,
    "ic95_inferior_sensibilidad": 0.62, "falsas_alarmas_por_dia_sensor": 1.1,
    "mediana_anticipacion_min": 9.0, "ap_random_forest": 0.71, "ap_mejor_baseline": 0.55,
}
_VARIABLES = ("IA_PREVENTIVO_MODO", "IA_PREVENTIVO_VERSION", "IA_PREVENTIVO_SHA256", "IA_LEGADO_MODO")


def _registrar_preventivo(raiz, version=VERSION, **opciones) -> str:
    return registrar_preventivo(raiz, version, **opciones)


def _cuerpo_version(version: str, model_hash: str, **cambios) -> dict:
    return {
        "version": version, "model_hash": model_hash, "feature_schema_version": "termico_minimo",
        "dataset_version": "corridas-2026-11", "scikit_learn_version": sklearn.__version__,
        "python_version": "3.12.13", "trained_at": "2026-11-01T00:00:00Z", "horizonte_min": 30,
        "metricas": _METRICAS, "particiones": {"entrenamiento": "C1,C2", "prueba": "C3", "separacion_min": 30},
        **cambios,
    }


def _configurar(**variables) -> None:
    os.environ.update(variables)
    get_settings.cache_clear()


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Registro temporal con V aprobada y la configuración de la fase A
    (IA_PREVENTIVO_MODO=activo con la versión y el SHA-256 de V)."""
    sha = _registrar_preventivo(tmp_path)
    anteriores = {k: os.environ.get(k) for k in _VARIABLES}
    _configurar(IA_PREVENTIVO_MODO="activo", IA_PREVENTIVO_VERSION=VERSION, IA_PREVENTIVO_SHA256=sha,
                IA_LEGADO_MODO="experimental")
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


def _registrar_y_activar(client, token, sha) -> dict:
    assert client.post(
        "/api/ia/modelo/versiones", json=_cuerpo_version(VERSION, sha), headers=auth_header(token)
    ).status_code == 201
    respuesta = client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token))
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _desactivar(client, token, version=VERSION, motivo=MOTIVO):
    return client.post(
        f"/api/ia/modelo/versiones/{version}/desactivar", json={"motivo": motivo}, headers=auth_header(token)
    )


def _activa(client, token) -> str | None:
    versiones = client.get("/api/ia/modelo/versiones", headers=auth_header(token)).json()
    activas = [v["version"] for v in versiones if v["activa"]]
    assert len(activas) <= 1
    return activas[0] if activas else None


async def _eventos(factory, tipo: str) -> list[TraceabilityRecordModel]:
    async with factory() as session:
        filas = (await session.execute(
            select(TraceabilityRecordModel).where(TraceabilityRecordModel.tipo_evento == tipo)
        )).scalars().all()
    return sorted(filas, key=lambda f: f.chain_seq)


async def _auditoria(factory, accion: str) -> list[AuditLogModel]:
    async with factory() as session:
        filas = (await session.execute(select(AuditLogModel).where(AuditLogModel.accion == accion))).scalars().all()
    return list(filas)


# ── 200: la versión queda inactiva y no queda ninguna activa ────────────────


def test_desactivar_la_version_activa_responde_200_y_la_deja_inactiva(client, token_admin, entorno):
    servicio, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)
    assert servicio.disponible is True

    respuesta = _desactivar(client, token_admin)

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["version"] == VERSION and cuerpo["activa"] is False
    assert cuerpo["desactivada_en"] is not None and cuerpo["activada_en"] is not None
    assert _activa(client, token_admin) is None
    # En el mismo proceso: el preventivo deja de cargarse sin reiniciar.
    assert servicio.disponible is False
    assert servicio.motivo_codigo() == "sin_version_activa_registrada"


def test_tras_desactivar_health_dice_sin_version_activa(client, token_admin, entorno):
    _, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)
    assert _desactivar(client, token_admin).status_code == 200

    modelo = client.get("/health/detalle", headers=auth_header(token_admin)).json()["modelo_ia"]

    assert modelo["preventivo"]["disponible"] is False
    assert modelo["preventivo"]["motivo_codigo"] == "sin_version_activa_registrada"
    assert modelo["preventivo"]["es_version_activa_registrada"] is False
    assert modelo["version_activa_registrada"] is None
    assert modelo["en_uso"]["aprobado"] is False and modelo["en_uso"]["disponible"] is False
    # El legado sigue cargado como respaldo experimental, sin rechazo.
    assert modelo["disponible"] is True and modelo["motivo_codigo"] is None


async def test_se_puede_volver_a_activar_despues_de_desactivar(client, token_admin, entorno, db_session_factory):
    servicio, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)
    assert _desactivar(client, token_admin).status_code == 200

    respuesta = client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 200 and respuesta.json()["activa"] is True
    assert respuesta.json()["desactivada_en"] is None
    assert servicio.disponible is True
    ultima = (await _eventos(db_session_factory, "MODELO_IA_VERSION_ACTIVADA"))[-1]
    assert ultima.payload["version_anterior"] is None, "tras desactivar no había versión activa"


# ── Evento encadenado y bitácora ────────────────────────────────────────────


async def test_desactivar_encadena_el_evento_y_deja_la_bitacora(client, token_admin, entorno, db_session_factory):
    _, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)
    cuerpo = _desactivar(client, token_admin).json()

    async with db_session_factory() as session:
        admin = (await session.execute(
            select(UserModel).where(UserModel.email == "admin@farmacia.example.org")
        )).scalar_one()
        cadena = (await session.execute(
            select(TraceabilityRecordModel)
            .where(TraceabilityRecordModel.chain_id == "SISTEMA")
            .order_by(TraceabilityRecordModel.chain_seq)
        )).scalars().all()
    (evento,) = [e for e in cadena if e.tipo_evento == "MODELO_IA_VERSION_DESACTIVADA"]

    # Quién, qué versión, por qué y con qué artefacto.
    assert evento.usuario_id == admin.id
    assert evento.payload["admin_id"] == str(admin.id)
    assert evento.payload["version"] == VERSION
    assert evento.payload["motivo"] == MOTIVO
    assert evento.payload["model_hash"] == sha
    assert evento.payload["modelo"] == "preventivo"
    assert evento.payload["version_activa_resultante"] is None
    # Cuándo: el instante del eslabón es el de desactivada_en.
    desactivada_en = datetime.fromisoformat(cuerpo["desactivada_en"].replace("Z", "+00:00"))
    ts = evento.timestamp if evento.timestamp.tzinfo else evento.timestamp.replace(tzinfo=timezone.utc)
    if desactivada_en.tzinfo is None:
        desactivada_en = desactivada_en.replace(tzinfo=timezone.utc)
    assert ts == desactivada_en
    # Encadenado al eslabón anterior de la cadena SISTEMA (hash anterior).
    anterior = next(e for e in cadena if e.chain_seq == evento.chain_seq - 1)
    assert evento.previous_hash == anterior.hash_actual
    async with db_session_factory() as session:
        verificacion = await VerificarIntegridadRegistroUseCase(
            SQLAlchemyTrazabilidadRepository(session)
        ).execute(chain_id="SISTEMA")
    assert verificacion.integra is True

    (fila,) = await _auditoria(db_session_factory, "DESACTIVAR_VERSION_MODELO_IA")
    assert fila.usuario_id == admin.id
    assert fila.detalle["version"] == VERSION and fila.detalle["motivo"] == MOTIVO
    assert fila.recurso == f"ia/modelo/versiones/{VERSION}/desactivar"


# ── 404 / 409 / 422 / 403 ───────────────────────────────────────────────────


async def test_desactivar_una_version_no_registrada_es_404_y_se_audita(
    client, token_admin, entorno, db_session_factory
):
    _, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)

    respuesta = _desactivar(client, token_admin, version="no-existe-0.0.0")

    assert respuesta.status_code == 404
    assert _activa(client, token_admin) == VERSION
    (fila,) = await _auditoria(db_session_factory, "DESACTIVAR_VERSION_MODELO_IA_RECHAZADO")
    assert fila.detalle["motivo"] == "version_no_registrada" and fila.resultado == "fallido"
    assert await _eventos(db_session_factory, "MODELO_IA_VERSION_DESACTIVADA") == []


async def test_desactivar_una_version_que_no_es_la_activa_es_409(client, token_admin, entorno, db_session_factory):
    _, sha, _ = entorno
    # Registrada pero nunca activada.
    client.post("/api/ia/modelo/versiones", json=_cuerpo_version(VERSION, sha), headers=auth_header(token_admin))

    respuesta = _desactivar(client, token_admin)

    assert respuesta.status_code == 409
    assert respuesta.json()["detail"] == "version_no_activa"
    (fila,) = await _auditoria(db_session_factory, "DESACTIVAR_VERSION_MODELO_IA_RECHAZADO")
    assert fila.detalle["motivo"] == "version_no_activa"
    assert await _eventos(db_session_factory, "MODELO_IA_VERSION_DESACTIVADA") == []


async def test_la_segunda_desactivacion_es_409_y_no_duplica_el_evento(
    client, token_admin, entorno, db_session_factory
):
    _, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)

    primera = _desactivar(client, token_admin)
    segunda = _desactivar(client, token_admin)

    assert (primera.status_code, segunda.status_code) == (200, 409)
    assert segunda.json()["detail"] == "version_no_activa"
    assert len(await _eventos(db_session_factory, "MODELO_IA_VERSION_DESACTIVADA")) == 1
    assert len(await _auditoria(db_session_factory, "DESACTIVAR_VERSION_MODELO_IA")) == 1


@pytest.mark.parametrize(
    "cuerpo",
    [
        None,
        {},
        {"motivo": "corto"},
        {"motivo": "    nueve    "},
        {"motivo": "x" * 501},
        {"motivo": None},
        {"motivo": MOTIVO, "version": VERSION},
    ],
    ids=["sin_cuerpo", "sin_motivo", "menos_de_10", "espacios_no_cuentan", "mas_de_500", "nulo", "campo_extra"],
)
async def test_motivo_invalido_es_422_y_no_desactiva(client, token_admin, entorno, db_session_factory, cuerpo):
    _, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)

    respuesta = client.post(
        f"/api/ia/modelo/versiones/{VERSION}/desactivar", json=cuerpo, headers=auth_header(token_admin)
    )

    assert respuesta.status_code == 422
    assert _activa(client, token_admin) == VERSION
    assert await _eventos(db_session_factory, "MODELO_IA_VERSION_DESACTIVADA") == []


def test_los_limites_del_motivo_se_aceptan(client, token_admin, entorno):
    _, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)
    assert _desactivar(client, token_admin, motivo="  " + "x" * 500 + "  ").status_code == 200
    client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))
    assert _desactivar(client, token_admin, motivo="0123456789").status_code == 200


async def test_solo_el_administrador_puede_desactivar(
    client, token_admin, token_farmaceutico, token_auditor, token_tecnico, entorno, db_session_factory
):
    _, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)

    respuestas = [_desactivar(client, token) for token in (token_farmaceutico, token_auditor, token_tecnico)]

    assert [r.status_code for r in respuestas] == [403, 403, 403]
    assert _activa(client, token_admin) == VERSION
    assert await _eventos(db_session_factory, "MODELO_IA_VERSION_DESACTIVADA") == []
    denegados = await _auditoria(db_session_factory, "RBAC_DENEGADO")
    assert len([f for f in denegados if "desactivar" in f.recurso]) == 3


# ── Línea base restaurada ───────────────────────────────────────────────────


async def _ingestar(factory, servicio, device: str) -> LecturaTermica:
    """20 min estables en 5 °C y luego una subida de 0,12 °C/min (sin salir de
    2-8 °C). Devuelve la última lectura."""
    temps = [5.0] * 20 + [5.0 + 0.12 * i for i in range(1, 6)]
    base = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(hours=2)
    ultima = None
    for i, temp in enumerate(temps):
        async with factory() as session:
            repo = SQLAlchemyLecturaRepository(session)
            ultima = await RegistrarLecturaTermicaUseCase(
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
            ))
            await session.commit()
    return ultima


def _comportamiento(lectura: LecturaTermica) -> dict:
    return {
        "riesgo_efectivo": lectura.riesgo_efectivo,
        "origen_clasificacion": lectura.origen_clasificacion,
        "modelo_version": lectura.modelo_version,
        "estado_inferencia": lectura.estado_inferencia,
        "ia_experimental": bool((lectura.payload or {}).get("ia_experimental")),
        "estado_riesgo": lectura.estado_riesgo(),
    }


async def test_tras_desactivar_el_riesgo_efectivo_vuelve_a_la_linea_base(
    client, token_admin, entorno, db_session_factory
):
    servicio, sha, _ = entorno
    antes = await _ingestar(db_session_factory, servicio, "ESP32-P12-ANTES")
    _registrar_y_activar(client, token_admin, sha)
    activa = await _ingestar(db_session_factory, servicio, "ESP32-P12-ACTIVA")
    assert _desactivar(client, token_admin).status_code == 200

    despues = await _ingestar(db_session_factory, servicio, "ESP32-P12-DESPUES")

    # Con la versión activa decidía el preventivo...
    assert activa.riesgo_efectivo == NivelRiesgo.RIESGO_PREVENTIVO and activa.modelo_version == VERSION
    # ...y tras desactivarla todo es igual que antes de cualquier activación.
    assert _comportamiento(despues) == _comportamiento(antes)
    assert despues.riesgo_efectivo == NivelRiesgo.NORMAL
    assert despues.modelo_version != VERSION


async def test_desactivar_devuelve_el_legado_a_su_verificacion_de_siempre(
    client, token_admin, entorno, db_session_factory, monkeypatch
):
    """El caso del hecho 5 del plan: V activa y el preventivo apagado por
    entorno (R-A). El arranque fija el hash de V al legado y lo rechaza; al
    desactivar V el legado vuelve a cargar y el estado es la línea base."""
    _, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)
    # R-A + reinicio: preventivo desactivado, la versión y el SHA quedan como estaban.
    _configurar(IA_PREVENTIVO_MODO="desactivado")
    legado = RandomForestRiesgoService()
    monkeypatch.setattr(rf, "_instancia", legado)
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    await main._aplicar_version_activa()
    assert legado.motivo_codigo() == "hash_distinto_de_la_version_activa"
    assert client.get("/health/detalle", headers=auth_header(token_admin)).json()["status"] == "degradado"

    assert _desactivar(client, token_admin).status_code == 200

    assert legado.motivo_codigo() is None and legado.modelo_disponible is True
    cuerpo = client.get("/health/detalle", headers=auth_header(token_admin)).json()
    assert cuerpo["status"] == "ok"
    assert cuerpo["modelo_ia"]["en_uso"]["modelo"] == "regla_2_8"
    assert cuerpo["modelo_ia"]["version_activa_registrada"] is None
    evento = (await _eventos(db_session_factory, "MODELO_IA_VERSION_DESACTIVADA"))[0]
    assert evento.payload["modelo"] == "legado", "con el preventivo apagado la versión gobernaba el legado"
    # Otro reinicio: sin versión activa nada fija un hash al legado.
    reiniciado = RandomForestRiesgoService()
    monkeypatch.setattr(rf, "_instancia", reiniciado)
    await main._aplicar_version_activa()
    assert reiniciado.hash_aprobado is None and reiniciado.modelo_disponible is True


# ── HU-47 esc. 2: la historia de periodos incluye las desactivaciones ───────


async def test_una_lectura_posterior_a_la_desactivacion_no_cruza_con_la_version(
    client, token_admin, token_farmaceutico, entorno, db_session_factory
):
    servicio, sha, _ = entorno
    _registrar_y_activar(client, token_admin, sha)
    assert _desactivar(client, token_admin).status_code == 200

    lectura = await _ingestar(db_session_factory, servicio, "ESP32-P12-HU47")
    cuerpo = client.get(f"/api/ia/inferencia/{lectura.id}", headers=auth_header(token_farmaceutico)).json()

    assert cuerpo["version_activa_en_ese_momento"] is None
    assert cuerpo["version_era_activa_y_aprobada_en_ese_momento"] is not True
    assert cuerpo["politica_operativa"] != "aprobada"


async def test_la_version_activa_en_un_instante_respeta_la_desactivacion(client, token_admin, entorno, db_session_factory):
    _, sha, _ = entorno
    activada = _registrar_y_activar(client, token_admin, sha)
    desactivada = _desactivar(client, token_admin).json()

    def _instante(texto: str) -> datetime:
        valor = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)

    t_activa, t_desactiva = _instante(activada["activada_en"]), _instante(desactivada["desactivada_en"])
    async with db_session_factory() as session:
        repositorio = SQLAlchemyModeloIARepository(session)
        durante = await repositorio.obtener_version_activa_en(t_activa + (t_desactiva - t_activa) / 2)
        despues = await repositorio.obtener_version_activa_en(t_desactiva + timedelta(seconds=1))

    assert durante is not None and durante.version == VERSION
    assert despues is None


# ── IA-B05 v6 y v7 por la API: 409 con un código por motivo ─────────────────


async def test_b05_v6_horizonte_distinto_es_409_con_su_codigo(client, token_admin, entorno, db_session_factory):
    _, sha, _ = entorno
    client.post(
        "/api/ia/modelo/versiones", json=_cuerpo_version(VERSION, sha, horizonte_min=15),
        headers=auth_header(token_admin),
    )

    respuesta = client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 409
    assert respuesta.json()["codigos"] == ["horizonte_distinto_del_manifiesto"]
    assert "horizonte registrado (15 min)" in respuesta.json()["detail"]
    assert _activa(client, token_admin) is None
    (fila,) = await _auditoria(db_session_factory, "ACTIVAR_VERSION_MODELO_IA_RECHAZADO")
    assert fila.detalle["codigos"] == ["horizonte_distinto_del_manifiesto"]
    assert await _eventos(db_session_factory, "MODELO_IA_VERSION_ACTIVADA") == []


def test_b05_v6_sklearn_registrado_distinto_es_409_con_su_codigo(client, token_admin, entorno):
    _, sha, _ = entorno
    client.post(
        "/api/ia/modelo/versiones", json=_cuerpo_version(VERSION, sha, scikit_learn_version="1.8.2"),
        headers=auth_header(token_admin),
    )

    respuesta = client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 409
    assert respuesta.json()["codigos"] == ["sklearn_distinto_del_manifiesto"]


def test_b05_v6_sklearn_distinto_del_instalado_es_409_con_su_codigo(client, token_admin, tmp_path, monkeypatch):
    version = "rf-preventivo-p12-sk-1.0.0"
    sha = _registrar_preventivo(tmp_path, version, sklearn_manifiesto="1.0.2")
    anteriores = {k: os.environ.get(k) for k in _VARIABLES}
    _configurar(IA_PREVENTIVO_MODO="activo", IA_PREVENTIVO_VERSION=version, IA_PREVENTIVO_SHA256=sha)
    monkeypatch.setattr(modulo_servicio, "_instancia", ServicioPrediccionPreventiva(MODO_ACTIVO, version, sha, raiz=tmp_path))
    try:
        client.post(
            "/api/ia/modelo/versiones", json=_cuerpo_version(version, sha, scikit_learn_version="1.0.2"),
            headers=auth_header(token_admin),
        )
        respuesta = client.post(f"/api/ia/modelo/versiones/{version}/activar", headers=auth_header(token_admin))
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        get_settings.cache_clear()

    assert respuesta.status_code == 409
    assert respuesta.json()["codigos"] == ["sklearn_incompatible_con_el_instalado"]


def test_b05_v7_manifiesto_editado_a_mano_es_409_con_su_codigo(client, token_admin, tmp_path, monkeypatch):
    version = "rf-preventivo-p12-mano-1.0.0"
    sha = _registrar_preventivo(tmp_path, version, aprobar=False)
    ruta = registro.ruta_manifiesto(version, tmp_path)
    manifiesto = json.loads(ruta.read_text(encoding="utf-8"))
    manifiesto["estado"] = ESTADO_APROBADO
    ruta.write_text(json.dumps(manifiesto, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    anteriores = {k: os.environ.get(k) for k in _VARIABLES}
    _configurar(IA_PREVENTIVO_MODO="activo", IA_PREVENTIVO_VERSION=version, IA_PREVENTIVO_SHA256=sha)
    monkeypatch.setattr(modulo_servicio, "_instancia", ServicioPrediccionPreventiva(MODO_ACTIVO, version, sha, raiz=tmp_path))
    try:
        client.post("/api/ia/modelo/versiones", json=_cuerpo_version(version, sha), headers=auth_header(token_admin))
        respuesta = client.post(f"/api/ia/modelo/versiones/{version}/activar", headers=auth_header(token_admin))
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        get_settings.cache_clear()

    assert respuesta.status_code == 409
    assert respuesta.json()["codigos"] == ["manifiesto_distinto_del_historial"]


def test_b03_los_criterios_tambien_traen_codigo(client, token_admin, entorno):
    _, sha, _ = entorno
    metricas = {**_METRICAS, "incidentes_evaluables": 9}
    client.post(
        "/api/ia/modelo/versiones", json=_cuerpo_version(VERSION, sha, metricas=metricas),
        headers=auth_header(token_admin),
    )

    respuesta = client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 409
    assert respuesta.json()["codigos"] == ["no_cumple_criterio_principal"]
    assert "no cumple el criterio principal" in respuesta.json()["detail"]
