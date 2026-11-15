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
from src.domain.value_objects.motivo_bloqueo import MotivoBloqueo
from src.infrastructure.ai.prediccion_preventiva.servicio import (
    ACTIVACION_ARTEFACTO_DISTINTO,
    MODO_ACTIVO,
    MOTIVO_ARTEFACTO_DISTINTO,
    ServicioPrediccionPreventiva,
    es_rechazo_critico,
    obtener_servicio_preventivo,
)
from src.infrastructure.ai.random_forest_service import CODIGOS_RECHAZO as CODIGOS_RECHAZO_LEGADO
from src.infrastructure.ai.random_forest_service import get_random_forest_service

MODELO_PREVENTIVO = "preventivo"
MODELO_LEGADO = "legado"


def modelo_gobernado(settings) -> str:
    """Qué modelo aprueba la versión activa del registro."""
    return MODELO_PREVENTIVO if settings.ia_preventivo_modo == MODO_ACTIVO else MODELO_LEGADO


def modo_legado_efectivo(settings) -> str:
    """HU-46 («solo modelos aprobados clasifiquen en producción») y HU-16.

    `IA_LEGADO_MODO=activo` deja que el Random Forest legado decida el riesgo
    efectivo y abra alertas preventivas SOLO si es el modelo que gobierna el
    registro y la versión activa registrada fijó su hash aprobado. En otro caso
    (el preventivo es el gobernado, o no hay versión activa registrada) se
    degrada a `experimental`: su clase queda como evidencia marcada y no
    decide. Los modos `experimental` y `desactivado` no cambian."""
    modo = settings.ia_legado_modo
    if modo != "activo":
        return modo
    if modelo_gobernado(settings) == MODELO_PREVENTIVO:
        return "experimental"
    if get_random_forest_service().hash_aprobado is None:
        return "experimental"
    return modo


def rechazo_es_critico(settings, modelo: str, codigo: str | None) -> bool:
    """HU-46 esc. 3: ¿el rechazo de este modelo al arrancar es un error crítico?
    Lo es el RECHAZO del artefacto (alterado, otro hash u otra versión, no
    aprobado, incompatible) del modelo configurado para decidir el riesgo
    (modo activo). Sin versión activa (p. ej. tras /desactivar), sin variables
    o en sombra/experimental es un aviso. Mismo criterio que el servicio
    preventivo (`es_rechazo_critico`)."""
    if modelo == MODELO_PREVENTIVO:
        return es_rechazo_critico(settings.ia_preventivo_modo, codigo)
    return settings.ia_legado_modo == "activo" and codigo in CODIGOS_RECHAZO_LEGADO


def servicio_preventivo(settings) -> ServicioPrediccionPreventiva:
    return obtener_servicio_preventivo(
        settings.ia_preventivo_modo, settings.ia_preventivo_version, settings.ia_preventivo_sha256
    )


def verificador_de_activacion(settings) -> Callable[[VersionModeloIA], list[str]]:
    """HU-16 esc. 4: motivos por los que el artefacto que se cargará NO es el
    de la versión que se quiere activar (lista vacía = es el mismo)."""
    if modelo_gobernado(settings) == MODELO_PREVENTIVO:
        preventivo = servicio_preventivo(settings)
        # IA-B05 v6: H y la versión de scikit-learn que digitó el admin se
        # contrastan con el manifiesto que de verdad se cargará.
        return lambda version: preventivo.motivos_activacion(
            version.version, version.model_hash,
            horizonte_min=version.horizonte_min, scikit_learn_version=version.scikit_learn_version,
        )
    legado = get_random_forest_service()
    return lambda version: (
        [] if legado.hash_artefacto() == version.model_hash
        else [MotivoBloqueo(ACTIVACION_ARTEFACTO_DISTINTO, MOTIVO_ARTEFACTO_DISTINTO)]
    )


def aplicar_version_activa(settings, activa: VersionModeloIA | None) -> None:
    """Al arrancar, tras cada activación y tras desactivar: desde aquí solo se
    carga el artefacto de la versión activa (HU-46 esc. 3).

    Sin versión activa (`activa=None`, p. ej. después de
    POST /desactivar) se vuelve a la línea base: el preventivo en modo activo
    no carga (`sin_version_activa_registrada`) y el legado deja de exigir el
    hash de una versión registrada y vuelve a verificarse con su metadata
    externa, como antes de cualquier activación (P-12)."""
    servicio_preventivo(settings).fijar_version_activa(
        (activa.version, activa.model_hash) if activa is not None else None
    )
    if modelo_gobernado(settings) != MODELO_LEGADO:
        return
    legado = get_random_forest_service()
    if activa is not None:
        legado.fijar_hash_aprobado(activa.model_hash)
    elif legado.hash_aprobado is not None:
        legado.fijar_hash_aprobado(None)
