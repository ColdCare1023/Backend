"""HU-16 / HU-46: qué artefacto aprueba el registro de versiones de la base.

El registro (`ai_model_versions`) tiene UNA versión activa. Esa versión
aprueba el artefacto que realmente clasifica:

- con `IA_PREVENTIVO_MODO=activo`, el modelo preventivo de
  `models/preventivo/<IA_PREVENTIVO_VERSION>/` (solo carga si la versión y el
  SHA-256 configurados son los de la versión activa);
- en otro caso, el Random Forest legado (`random_forest_termico.pkl`).

Con el preventivo activo, el legado queda como respaldo experimental (no
abre alertas preventivas con `IA_LEGADO_MODO=experimental`) y se verifica con
su metadata externa, como hoy en producción: el hash de la versión activa no
es el suyo y aplicárselo lo rechazaría sin motivo.
"""

from collections.abc import Callable

from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.infrastructure.ai.prediccion_preventiva.servicio import (
    MODO_ACTIVO,
    MOTIVO_ARTEFACTO_DISTINTO,
    ServicioPrediccionPreventiva,
    obtener_servicio_preventivo,
)
from src.infrastructure.ai.random_forest_service import get_random_forest_service

MODELO_PREVENTIVO = "preventivo"
MODELO_LEGADO = "legado"


def modelo_gobernado(settings) -> str:
    """Qué modelo aprueba la versión activa del registro."""
    return MODELO_PREVENTIVO if settings.ia_preventivo_modo == MODO_ACTIVO else MODELO_LEGADO


def servicio_preventivo(settings) -> ServicioPrediccionPreventiva:
    return obtener_servicio_preventivo(
        settings.ia_preventivo_modo, settings.ia_preventivo_version, settings.ia_preventivo_sha256
    )


def verificador_de_activacion(settings) -> Callable[[VersionModeloIA], list[str]]:
    """HU-16 esc. 4: motivos por los que el artefacto que se cargará NO es el
    de la versión que se quiere activar (lista vacía = es el mismo)."""
    if modelo_gobernado(settings) == MODELO_PREVENTIVO:
        preventivo = servicio_preventivo(settings)
        return lambda version: preventivo.motivos_activacion(version.version, version.model_hash)
    legado = get_random_forest_service()
    return lambda version: [] if legado.hash_artefacto() == version.model_hash else [MOTIVO_ARTEFACTO_DISTINTO]


def aplicar_version_activa(settings, activa: VersionModeloIA | None) -> None:
    """Al arrancar y tras cada activación: desde aquí solo se carga el
    artefacto de la versión activa (HU-46 esc. 3)."""
    servicio_preventivo(settings).fijar_version_activa(
        (activa.version, activa.model_hash) if activa is not None else None
    )
    if modelo_gobernado(settings) == MODELO_LEGADO and activa is not None:
        get_random_forest_service().fijar_hash_aprobado(activa.model_hash)
