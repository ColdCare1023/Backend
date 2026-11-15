"""IA-B05 variantes 6 y 7 y HU-46 esc. 3 (ronda 2b, pista «gobierno»).

- v6: /activar contrasta lo que el admin registró (H y versión de
  scikit-learn) con el manifiesto que de verdad se cargará, y exige que ese
  manifiesto se pueda cargar con el scikit-learn instalado.
- v7: un manifiesto editado a mano (sin pasar por la CLI, que anota su SHA-256
  en historial.json) no se activa aunque diga APPROVED_FOR_DEPLOYMENT.
- HU-46 esc. 3: un artefacto preventivo rechazado (alterado, otro hash, otra
  versión, incompatible) queda como error CRITICAL en el log; los estados de
  configuración (modo desactivado, ninguna versión activa) siguen como aviso.

Cada motivo lleva un código estable (`MotivoBloqueo.codigo`) y sigue siendo el
texto de siempre.
"""

import json
import logging
import re
from pathlib import Path

import numpy as np
import pytest
import sklearn
from sklearn.ensemble import RandomForestClassifier

from src.domain.value_objects.motivo_bloqueo import MotivoBloqueo, codigo_de
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva import servicio as modulo_servicio
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    ESTADO_APROBADO,
    ESTADO_VALIDADO_OFFLINE,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import nombres_features
from src.infrastructure.ai.prediccion_preventiva.servicio import (
    ACTIVACION_HORIZONTE_DISTINTO,
    ACTIVACION_MANIFIESTO_DISTINTO_DEL_HISTORIAL,
    ACTIVACION_MANIFIESTO_NO_APROBADO,
    ACTIVACION_SKLEARN_DISTINTO_DEL_MANIFIESTO,
    ACTIVACION_SKLEARN_INCOMPATIBLE,
    CODIGO_ARTEFACTO_NO_VERIFICADO,
    CODIGO_ESTADO_NO_ADMITIDO,
    CODIGO_HASH_NO_ES_EL_REGISTRADO,
    CODIGO_INCOMPATIBLE,
    CODIGO_MODO_DESACTIVADO,
    CODIGO_SHA256_NO_CONFIGURADO,
    CODIGO_SIN_VERSION_ACTIVA,
    CODIGO_VERSION_NO_CONFIGURADA,
    CODIGO_VERSION_NO_ES_LA_ACTIVA,
    MODO_ACTIVO,
    MODO_DESACTIVADO,
    ServicioPrediccionPreventiva,
)

VERSION = "rf-b05-1.0.0"
CONFIG = ConfiguracionPreventiva()


def _registrar(raiz, version=VERSION, *, sklearn_manifiesto: str | None = None, aprobar: bool = True) -> str:
    nombres = nombres_features(CONFIG)
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, (200, len(nombres)))
    modelo = RandomForestClassifier(n_estimators=5, random_state=0).fit(x, (x[:, 0] > 0).astype(int))
    registro.registrar(modelo, {
        "version": version, "estado": "EXPERIMENTAL", "conjunto_features": "termico_minimo",
        "features": nombres, "configuracion": CONFIG.como_dict(), "umbral_decision": 0.5,
        "puntuacion_calibrada": False,
        "versiones": {"scikit_learn": sklearn_manifiesto or sklearn.__version__},
    }, raiz)
    if aprobar:
        registro.cambiar_estado(version, ESTADO_VALIDADO_OFFLINE, "prueba", "qa", None, raiz)
        evidencia = raiz / f"exp_d_{version}.json"
        evidencia.write_text("{}", encoding="utf-8")
        registro.cambiar_estado(version, ESTADO_APROBADO, "prueba", "qa", evidencia, raiz)
    return registro.leer_manifiesto(version, raiz)["artefacto"]["sha256"]


def _editar_manifiesto_a_mano(raiz, version=VERSION, **cambios) -> None:
    ruta = registro.ruta_manifiesto(version, raiz)
    manifiesto = json.loads(ruta.read_text(encoding="utf-8"))
    manifiesto.update(cambios)
    ruta.write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _codigos(motivos) -> list[str]:
    return [codigo_de(m) for m in motivos]


# ── IA-B05 v6 y v7: /activar valida el manifiesto que se cargará ────────────


def test_todo_coherente_no_tiene_motivos(tmp_path):
    sha = _registrar(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    assert servicio.motivos_activacion(VERSION, sha, horizonte_min=30, scikit_learn_version=sklearn.__version__) == []


def test_v6_horizonte_registrado_distinto_del_manifiesto(tmp_path):
    sha = _registrar(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    motivos = servicio.motivos_activacion(VERSION, sha, horizonte_min=15, scikit_learn_version=sklearn.__version__)

    assert _codigos(motivos) == [ACTIVACION_HORIZONTE_DISTINTO]
    assert "15 min" in motivos[0] and "30 min" in motivos[0]


def test_v6_sklearn_registrado_distinto_del_manifiesto(tmp_path):
    sha = _registrar(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    motivos = servicio.motivos_activacion(VERSION, sha, horizonte_min=30, scikit_learn_version="1.8.2")

    assert _codigos(motivos) == [ACTIVACION_SKLEARN_DISTINTO_DEL_MANIFIESTO]


def test_v6_manifiesto_entrenado_con_otro_sklearn_que_el_instalado(tmp_path):
    """El registro y el manifiesto coinciden (1.0.2), pero con el instalado no
    cargaría: la activación lo dice antes de dejar el modelo «no disponible»."""
    sha = _registrar(tmp_path, sklearn_manifiesto="1.0.2")
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    motivos = servicio.motivos_activacion(VERSION, sha, horizonte_min=30, scikit_learn_version="1.0.2")

    assert _codigos(motivos) == [ACTIVACION_SKLEARN_INCOMPATIBLE]


def test_v6_misma_version_menor_de_sklearn_se_admite(tmp_path):
    """Misma regla que la carga (versión menor): un parche distinto no bloquea."""
    mayor, menor = sklearn.__version__.split(".")[:2]
    parche = f"{mayor}.{menor}.999"
    sha = _registrar(tmp_path, sklearn_manifiesto=parche)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    assert servicio.motivos_activacion(VERSION, sha, horizonte_min=30, scikit_learn_version=parche) == []


def test_v7_manifiesto_editado_a_mano_a_approved_no_se_activa(tmp_path):
    sha = _registrar(tmp_path, aprobar=False)
    _editar_manifiesto_a_mano(tmp_path, estado=ESTADO_APROBADO)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    motivos = servicio.motivos_activacion(VERSION, sha, horizonte_min=30, scikit_learn_version=sklearn.__version__)

    # El estado dice APPROVED, pero el manifiesto no es el que anotó la CLI.
    assert _codigos(motivos) == [ACTIVACION_MANIFIESTO_DISTINTO_DEL_HISTORIAL]


def test_v7_sin_entrada_en_el_historial_no_se_activa(tmp_path):
    sha = _registrar(tmp_path)
    (tmp_path / registro.ARCHIVO_HISTORIAL).write_text("[]\n", encoding="utf-8")
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    assert _codigos(servicio.motivos_activacion(VERSION, sha)) == [ACTIVACION_MANIFIESTO_DISTINTO_DEL_HISTORIAL]


def test_el_historial_registra_el_ultimo_hash_del_manifiesto(tmp_path):
    _registrar(tmp_path)
    anotado = registro.sha256_manifiesto_en_historial(VERSION, tmp_path)

    assert anotado == registro.sha256_archivo(registro.ruta_manifiesto(VERSION, tmp_path))
    assert registro.sha256_manifiesto_en_historial("no-existe-1.0.0", tmp_path) is None


def test_los_modelos_del_repositorio_coinciden_con_su_historial():
    """Los manifiestos versionados en el repositorio son los que anotó la CLI."""
    for entrada in registro.listar():
        ruta = registro.ruta_manifiesto(entrada["version"])
        assert registro.sha256_archivo(ruta) == registro.sha256_manifiesto_en_historial(entrada["version"])


def test_un_manifiesto_no_aprobado_conserva_su_motivo(tmp_path):
    sha = _registrar(tmp_path, aprobar=False)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    motivos = servicio.motivos_activacion(VERSION, sha)

    assert _codigos(motivos) == [ACTIVACION_MANIFIESTO_NO_APROBADO]
    assert "APPROVED_FOR_DEPLOYMENT" in motivos[0]


def test_el_motivo_sigue_siendo_texto():
    motivo = MotivoBloqueo("codigo_x", "texto legible")
    assert motivo == "texto legible" and isinstance(motivo, str) and motivo.codigo == "codigo_x"
    assert "; ".join([motivo, "otro texto"]) == "texto legible; otro texto"
    assert codigo_de("sin código") == "otro"


# ── HU-46 esc. 3: el rechazo del artefacto es un error crítico ──────────────


@pytest.fixture
def registros_del_servicio(caplog, monkeypatch):
    # alembic.fileConfig (test_migraciones) deshabilita los loggers existentes.
    monkeypatch.setattr(modulo_servicio.logger, "disabled", False)
    monkeypatch.setattr(modulo_servicio.logger, "propagate", True)
    caplog.set_level(logging.DEBUG, logger=modulo_servicio.logger.name)
    return caplog


def _criticos(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == modulo_servicio.logger.name and r.levelno == logging.CRITICAL]


def _alterar_un_byte(raiz, version=VERSION) -> None:
    ruta = registro.directorio_version(version, raiz) / registro.ARCHIVO_MODELO
    contenido = bytearray(ruta.read_bytes())
    contenido[len(contenido) // 2] ^= 0xFF
    ruta.write_bytes(bytes(contenido))


def test_artefacto_alterado_se_registra_como_critico(tmp_path, registros_del_servicio):
    sha = _registrar(tmp_path)
    _alterar_un_byte(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path, version_activa=(VERSION, sha))

    assert servicio.disponible is False
    assert servicio.motivo_codigo() == CODIGO_ARTEFACTO_NO_VERIFICADO
    (critico,) = _criticos(registros_del_servicio)
    assert CODIGO_ARTEFACTO_NO_VERIFICADO in critico.getMessage()


@pytest.mark.parametrize(
    ("version_activa", "codigo"),
    [
        (("otra-2.0.0", None), CODIGO_VERSION_NO_ES_LA_ACTIVA),
        ((VERSION, "f" * 64), CODIGO_HASH_NO_ES_EL_REGISTRADO),
    ],
)
def test_version_u_hash_distintos_del_registro_se_registran_como_criticos(
    tmp_path, registros_del_servicio, version_activa, codigo
):
    sha = _registrar(tmp_path)
    activa = (version_activa[0], version_activa[1] or sha)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path, version_activa=activa)

    assert servicio.motivo_codigo() == codigo
    assert len(_criticos(registros_del_servicio)) == 1


def test_modelo_incompatible_se_registra_como_critico(tmp_path, registros_del_servicio):
    sha = _registrar(tmp_path)
    # Otra lista de features en el manifiesto: el artefacto pasa el hash pero no es compatible.
    _editar_manifiesto_a_mano(tmp_path, features=["otra_feature"])
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path, version_activa=(VERSION, sha))

    assert servicio.motivo_codigo() == CODIGO_INCOMPATIBLE
    assert len(_criticos(registros_del_servicio)) == 1


def test_sin_version_activa_es_un_aviso_no_un_error_critico(tmp_path, registros_del_servicio):
    """Tras desactivar la versión (P-12) no hay nada rechazado: es la línea base."""
    sha = _registrar(tmp_path)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=tmp_path)

    assert servicio.motivo_codigo() == CODIGO_SIN_VERSION_ACTIVA
    assert _criticos(registros_del_servicio) == []
    assert any(r.levelno == logging.WARNING for r in registros_del_servicio.records)


def test_modo_desactivado_no_registra_errores_criticos(tmp_path, registros_del_servicio):
    servicio = ServicioPrediccionPreventiva(MODO_DESACTIVADO, None, raiz=tmp_path)

    assert servicio.disponible is False
    assert _criticos(registros_del_servicio) == []


# ── P-9: la tabla de motivos de docs/operacion/IA_PREVENTIVA_ACTIVACION.md ──

DOC_ACTIVACION = Path(__file__).resolve().parents[2] / "docs" / "operacion" / "IA_PREVENTIVA_ACTIVACION.md"
ENCABEZADO_TABLA_MOTIVOS = "| `modelo_ia.preventivo.motivo_codigo` | Significado | Log |"


def _log_documentado() -> dict[str, str]:
    """Código -> columna «Log» de la tabla de motivos de no carga."""
    filas = DOC_ACTIVACION.read_text(encoding="utf-8").split(ENCABEZADO_TABLA_MOTIVOS, 1)[1].splitlines()[2:]
    documentado = {}
    for fila in filas:
        fila = fila.strip()
        if not fila.startswith("|"):
            break
        celdas = [celda.strip() for celda in fila.strip("|").split("|")]
        for codigo in re.findall(r"`([a-z0-9_]+)`", celdas[0]):
            documentado[codigo] = celdas[-1]
    return documentado


def _servicio_en_estado(raiz: Path, codigo: str) -> ServicioPrediccionPreventiva:
    if codigo == CODIGO_MODO_DESACTIVADO:
        return ServicioPrediccionPreventiva(MODO_DESACTIVADO, None, raiz=raiz)
    sha = _registrar(raiz, aprobar=codigo != CODIGO_ESTADO_NO_ADMITIDO)
    activa = (VERSION, sha)
    if codigo == CODIGO_VERSION_NO_CONFIGURADA:
        return ServicioPrediccionPreventiva(MODO_ACTIVO, None, sha, raiz=raiz, version_activa=activa)
    if codigo == CODIGO_SHA256_NO_CONFIGURADO:
        return ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, None, raiz=raiz, version_activa=activa)
    if codigo == CODIGO_SIN_VERSION_ACTIVA:
        activa = None
    elif codigo == CODIGO_VERSION_NO_ES_LA_ACTIVA:
        activa = ("otra-2.0.0", sha)
    elif codigo == CODIGO_HASH_NO_ES_EL_REGISTRADO:
        activa = (VERSION, "f" * 64)
    elif codigo == CODIGO_ARTEFACTO_NO_VERIFICADO:
        _alterar_un_byte(raiz)
    elif codigo == CODIGO_INCOMPATIBLE:
        _editar_manifiesto_a_mano(raiz, features=["otra_feature"])
    return ServicioPrediccionPreventiva(MODO_ACTIVO, VERSION, sha, raiz=raiz, version_activa=activa)


@pytest.mark.parametrize(
    "codigo",
    [
        CODIGO_MODO_DESACTIVADO, CODIGO_SIN_VERSION_ACTIVA, CODIGO_VERSION_NO_ES_LA_ACTIVA,
        CODIGO_HASH_NO_ES_EL_REGISTRADO, CODIGO_SHA256_NO_CONFIGURADO, CODIGO_VERSION_NO_CONFIGURADA,
        CODIGO_ARTEFACTO_NO_VERIFICADO, CODIGO_ESTADO_NO_ADMITIDO, CODIGO_INCOMPATIBLE,
    ],
)
def test_la_columna_log_del_documento_es_lo_que_registra_el_servicio(tmp_path, registros_del_servicio, codigo):
    """Cada motivo sale UNA vez en el log del proceso (la primera consulta del
    estado), con el nivel que dice el documento, y otra vez cuando una
    activación o desactivación obliga a reverificar. `modo_desactivado`, lo
    normal hoy en producción, también deja su WARNING."""
    documentado = _log_documentado()[codigo]
    servicio = _servicio_en_estado(tmp_path, codigo)

    for _ in range(3):
        assert servicio.disponible is False
    assert servicio.motivo_codigo() == codigo
    registros = [r for r in registros_del_servicio.records if r.name == modulo_servicio.logger.name]

    nivel = documentado.split()[0]
    assert nivel in {"WARNING", "CRITICAL"}, f"{codigo}: el documento dice «{documentado}»"
    assert [r.levelname for r in registros] == [nivel]
    assert codigo in registros[0].getMessage()

    # /activar o /desactivar fijan la versión activa y obligan a reverificar.
    servicio.fijar_version_activa(None)
    assert servicio.disponible is False
    assert len([r for r in registros_del_servicio.records if r.name == modulo_servicio.logger.name]) == 2
