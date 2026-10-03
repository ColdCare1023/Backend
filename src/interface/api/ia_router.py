from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.gestionar_modelo_ia import (
    ActivarVersionModeloUseCase,
    ConsultarInferenciaLecturaUseCase,
    RegistrarVersionModeloUseCase,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.value_objects.rol import Rol
from src.infrastructure.ai.features import FeaturesRiesgoTermico
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.modelo_ia_repository import SQLAlchemyModeloIARepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.schemas import (
    InferenciaLecturaResponse,
    RegistrarVersionModeloRequest,
    VersionModeloResponse,
)

router = APIRouter(prefix="/api/ia", tags=["ia"])


def _version_a_response(v: VersionModeloIA) -> VersionModeloResponse:
    return VersionModeloResponse(
        id=v.id,
        version=v.version,
        model_hash=v.model_hash,
        feature_schema_version=v.feature_schema_version,
        dataset_version=v.dataset_version,
        scikit_learn_version=v.scikit_learn_version,
        python_version=v.python_version,
        trained_at=v.trained_at,
        aprobado_por=v.aprobado_por,
        aprobado_en=v.aprobado_en,
        activa=v.activa,
        activada_en=v.activada_en,
        desactivada_en=v.desactivada_en,
    )


class ClasificacionRequest(BaseModel):
    """Vector de features para una clasificación de prueba (validación técnica)."""

    temperatura_ambiental: float = Field(ge=-40.0, le=125.0)
    humedad_ambiental: float = Field(ge=0.0, le=100.0)
    temperatura_interna: float = Field(ge=-55.0, le=125.0)
    diferencia_sensores: float
    duracion_fuera_rango: float = Field(ge=0.0, le=1440.0)
    frecuencia_desviaciones: float = Field(ge=0.0)
    tendencia_termica: float
    apertura_refrigerador: bool = False
    hora_evento: int = Field(ge=0, le=23)
    estado_conectividad_online: bool = True


class ClasificacionResponse(BaseModel):
    nivel_riesgo: str
    confianza: float
    origen: str


@router.get("/modelo")
async def obtener_metadata_modelo(
    _usuario=Depends(require_roles(Rol.FARMACEUTICO, Rol.AUDITOR)),
) -> dict:
    """Evidencia del modelo Random Forest para la validación técnica (RNF-04):
    metadatos del artefacto y métricas de entrenamiento (accuracy, precision,
    recall, F1 por clase, matriz de confusión y validación cruzada)."""
    servicio = get_random_forest_service()
    metricas = servicio.metricas_entrenamiento()
    if not servicio.modelo_disponible or metricas is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El modelo Random Forest no está entrenado en este entorno.",
        )
    return {
        "modelo_disponible": True,
        "metadata": servicio.metadata,
        "metricas": metricas,
    }


@router.post("/clasificar", response_model=ClasificacionResponse)
async def clasificar_prueba(
    body: ClasificacionRequest,
    _usuario=Depends(require_roles(Rol.FARMACEUTICO)),
) -> ClasificacionResponse:
    """Clasificación bajo demanda para las pruebas E2E de la tesis (RF-08):
    permite verificar la respuesta del clasificador ante un vector arbitrario
    sin registrar lecturas ni generar alertas."""
    servicio = get_random_forest_service()
    resultado = servicio.inferir(FeaturesRiesgoTermico(**body.model_dump()))
    return ClasificacionResponse(
        nivel_riesgo=resultado.nivel.value,
        confianza=round(resultado.confianza, 4),
        origen=resultado.origen,
    )


@router.post(
    "/modelo/versiones", response_model=VersionModeloResponse, status_code=status.HTTP_201_CREATED
)
async def registrar_version_modelo(
    body: RegistrarVersionModeloRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> VersionModeloResponse:
    """HU-46 Escenario 1: registra una versión aprobada del modelo. No la
    activa — ver POST /modelo/versiones/{version}/activar."""
    repositorio = SQLAlchemyModeloIARepository(session)
    use_case = RegistrarVersionModeloUseCase(
        repositorio, RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    try:
        registrada = await use_case.execute(
            version=body.version,
            model_hash=body.model_hash,
            feature_schema_version=body.feature_schema_version,
            dataset_version=body.dataset_version,
            scikit_learn_version=body.scikit_learn_version,
            python_version=body.python_version,
            trained_at=body.trained_at,
            admin_id=admin.id,
        )
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="REGISTRAR_VERSION_MODELO_IA",
        recurso=f"ia/modelo/versiones/{body.version}",
        detalle={"version": body.version, "model_hash": body.model_hash},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    return _version_a_response(registrada)


@router.get("/modelo/versiones", response_model=list[VersionModeloResponse])
async def listar_versiones_modelo(
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.ADMINISTRADOR, Rol.AUDITOR)),
) -> list[VersionModeloResponse]:
    repositorio = SQLAlchemyModeloIARepository(session)
    return [_version_a_response(v) for v in await repositorio.listar()]


@router.post("/modelo/versiones/{version}/activar", response_model=VersionModeloResponse)
async def activar_version_modelo(
    version: str,
    session: DbSessionDep,
    request: Request,
    admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> VersionModeloResponse:
    """HU-46 Escenario 2: activar una versión no registrada se rechaza (404)
    — la versión activa vigente permanece intacta."""
    repositorio = SQLAlchemyModeloIARepository(session)
    use_case = ActivarVersionModeloUseCase(
        repositorio, RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    try:
        activada = await use_case.execute(version, admin.id)
    except RecursoNoEncontradoError as exc:
        await AuditarAccionCriticaUseCase(
            SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
        ).execute(
            usuario_id=admin.id,
            accion="ACTIVAR_VERSION_MODELO_IA_RECHAZADO",
            recurso=f"ia/modelo/versiones/{version}/activar",
            detalle={"motivo": "version_no_registrada"},
            ip_origen=request.client.host if request.client else None,
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="ACTIVAR_VERSION_MODELO_IA",
        recurso=f"ia/modelo/versiones/{version}/activar",
        detalle={"version": version},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    return _version_a_response(activada)


@router.get("/inferencia/{lectura_id}", response_model=InferenciaLecturaResponse)
async def consultar_inferencia_lectura(
    lectura_id: UUID,
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.FARMACEUTICO, Rol.AUDITOR)),
) -> InferenciaLecturaResponse:
    """HU-47: qué versión del modelo, qué vector de features y qué
    probabilidad por clase produjeron la clasificación de una lectura
    concreta. Vista de solo lectura — no existe endpoint de escritura."""
    use_case = ConsultarInferenciaLecturaUseCase(
        SQLAlchemyLecturaRepository(session), SQLAlchemyModeloIARepository(session)
    )
    try:
        resultado = await use_case.execute(lectura_id)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return InferenciaLecturaResponse(**resultado)
