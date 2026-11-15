"""Ronda 2b: registrar con "activar": true (HU-46 esc. 1, ronda 2) pasa por las
mismas validaciones de /activar que agregó la 2b (IA-B05 v6 y v7) y responde
el mismo 409: `detail` en texto y `codigos` estables, en el mismo orden.

Si la activación se rechaza no queda nada a medias: la versión no se registra,
no hay eventos encadenados de registro ni de activación, y el rechazo queda en
la bitácora con sus códigos."""

import json
import os

import pytest
import sklearn
from sqlalchemy import select

from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva import servicio as modulo_servicio
from src.infrastructure.ai.prediccion_preventiva.configuracion import ESTADO_APROBADO
from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_ACTIVO, ServicioPrediccionPreventiva
from src.infrastructure.ai.random_forest_service import RandomForestRiesgoService
from src.infrastructure.config import get_settings
from src.infrastructure.database.models import AuditLogModel, TraceabilityRecordModel
from tests.conftest import auth_header
from tests.ia_preventiva import registrar_preventivo

VERSION = "rf-preventivo-r2b-reg-1.0.0"
_VARIABLES = ("IA_PREVENTIVO_MODO", "IA_PREVENTIVO_VERSION", "IA_PREVENTIVO_SHA256", "IA_LEGADO_MODO")
_METRICAS = {
    "f1_macro": 0.88, "incidentes_evaluables": 12, "sensibilidad_incidente": 0.92,
    "ic95_inferior_sensibilidad": 0.62, "falsas_alarmas_por_dia_sensor": 1.1,
    "mediana_anticipacion_min": 9.0, "ap_random_forest": 0.71, "ap_mejor_baseline": 0.55,
}


def _cuerpo(sha: str, **cambios) -> dict:
    return {
        "version": VERSION, "model_hash": sha, "feature_schema_version": "termico_minimo",
        "dataset_version": "corridas-2026-11", "scikit_learn_version": sklearn.__version__,
        "python_version": "3.12.13", "trained_at": "2026-11-01T00:00:00Z", "horizonte_min": 30,
        "metricas": _METRICAS, "particiones": {"entrenamiento": "C1,C2", "prueba": "C3", "separacion_min": 30},
        "activar": True, **cambios,
    }


def _editar_manifiesto_a_mano(raiz) -> None:
    """IA-B05 v7: APPROVED escrito a mano, sin pasar por la CLI."""
    ruta = registro.ruta_manifiesto(VERSION, raiz)
    manifiesto = json.loads(ruta.read_text(encoding="utf-8"))
    manifiesto["estado"] = ESTADO_APROBADO
    ruta.write_text(json.dumps(manifiesto, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def desplegado(tmp_path, monkeypatch):
    """Devuelve una función que despliega V (aprobada por la CLI o editada a
    mano) con IA_PREVENTIVO_MODO=activo y su SHA-256."""
    anteriores = {k: os.environ.get(k) for k in _VARIABLES}

    def _desplegar(aprobar: bool = True) -> str:
        sha = registrar_preventivo(tmp_path, VERSION, aprobar=aprobar)
        if not aprobar:
            _editar_manifiesto_a_mano(tmp_path)
        os.environ.update(
            IA_PREVENTIVO_MODO="activo", IA_PREVENTIVO_VERSION=VERSION, IA_PREVENTIVO_SHA256=sha,
            IA_LEGADO_MODO="experimental",
        )
        get_settings.cache_clear()
        monkeypatch.setattr(
            modulo_servicio, "_instancia", ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)
        )
        monkeypatch.setattr(rf, "_instancia", RandomForestRiesgoService())
        return sha

    try:
        yield _desplegar
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        get_settings.cache_clear()


async def _filas(factory, modelo) -> list:
    async with factory() as session:
        return list((await session.execute(select(modelo))).scalars().all())


@pytest.mark.parametrize(
    ("cambios", "aprobar", "codigo", "texto"),
    [
        pytest.param({"horizonte_min": 15}, True, "horizonte_distinto_del_manifiesto",
                     "horizonte registrado (15 min)", id="b05_v6_horizonte"),
        pytest.param({"scikit_learn_version": "1.8.2"}, True, "sklearn_distinto_del_manifiesto",
                     "scikit-learn registrada (1.8.2)", id="b05_v6_sklearn"),
        pytest.param({}, False, "manifiesto_distinto_del_historial",
                     "historial.json", id="b05_v7_manifiesto_a_mano"),
    ],
)
async def test_registrar_y_activar_aplica_las_validaciones_de_activar(
    client, token_admin, desplegado, db_session_factory, cambios, aprobar, codigo, texto
):
    sha = desplegado(aprobar)

    respuesta = client.post("/api/ia/modelo/versiones", json=_cuerpo(sha, **cambios), headers=auth_header(token_admin))

    assert respuesta.status_code == 409, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["codigos"] == [codigo]
    assert cuerpo["detail"].startswith("La versión no se registró ni se activó: ")
    assert texto in cuerpo["detail"]
    # Nada a medias: ni la fila de la versión ni sus eventos encadenados.
    assert client.get("/api/ia/modelo/versiones", headers=auth_header(token_admin)).json() == []
    tipos = {f.tipo_evento for f in await _filas(db_session_factory, TraceabilityRecordModel)}
    assert not tipos & {"MODELO_IA_VERSION_REGISTRADA", "MODELO_IA_VERSION_ACTIVADA"}
    (rechazo,) = [f for f in await _filas(db_session_factory, AuditLogModel)
                  if f.accion == "ACTIVAR_VERSION_MODELO_IA_RECHAZADO"]
    assert rechazo.detalle["codigos"] == [codigo]
    assert rechazo.detalle["al_registrar"] is True


def test_registrar_y_activar_coherente_sigue_respondiendo_201(client, token_admin, desplegado):
    sha = desplegado()

    respuesta = client.post("/api/ia/modelo/versiones", json=_cuerpo(sha), headers=auth_header(token_admin))

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["activa"] is True
