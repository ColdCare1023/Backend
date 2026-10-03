from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.consultar_alertas import (
    ConsultarAccionesCorrectivasUseCase,
    ConsultarAlertasUseCase,
    MarcarAlertaRevisadaUseCase,
)
from src.application.use_cases.registrar_accion_correctiva import (
    CorregirAccionCorrectivaUseCase,
    RegistrarAccionCorrectivaUseCase,
)
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.accion_correctiva_repository import (
    SQLAlchemyAccionCorrectivaRepository,
)
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.mappers import alerta_to_response
from src.interface.api.schemas import (
    AccionCorrectivaCreateRequest,
    AccionCorrectivaRectificarRequest,
    AccionCorrectivaResponse,
    AlertaResponse,
)

router = APIRouter(prefix="/api/alertas", tags=["alertas"])


@router.get("", response_model=list[AlertaResponse])
async def listar_alertas(
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
    device_id: str | None = None,
    revisada: bool | None = None,
    estado: str | None = None,
    limite: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[AlertaResponse]:
    alerta_repository = SQLAlchemyAlertaRepository(session)
    use_case = ConsultarAlertasUseCase(alerta_repository)
    alertas = await use_case.execute(
        device_id=device_id, revisada=revisada, estado=estado, limite=limite, offset=offset
    )
    return [alerta_to_response(a) for a in alertas]


@router.patch("/{alerta_id}/revisar", response_model=AlertaResponse)
async def revisar_alerta(
    alerta_id: UUID,
    session: DbSessionDep,
    request: Request,
    usuario=Depends(require_roles(Rol.FARMACEUTICO)),
) -> AlertaResponse:
    """HU-23 Escenario 1: PENDIENTE -> RECONOCIDA. El nombre del endpoint
    (/revisar) se conserva por compatibilidad; el efecto ahora es un
    reconocimiento formal con máquina de estados, no solo un booleano."""
    alerta_repository = SQLAlchemyAlertaRepository(session)
    use_case = MarcarAlertaRevisadaUseCase(alerta_repository)
    try:
        alerta = await use_case.execute(alerta_id, usuario.id)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    auditoria_repository = SQLAlchemyAuditLogRepository(session)
    await AuditarAccionCriticaUseCase(
        auditoria_repository, SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=usuario.id,
        accion="REVISAR_ALERTA",
        recurso=f"alertas/{alerta_id}",
        detalle={"nivel_riesgo": alerta.nivel_riesgo.value},
        ip_origen=request.client.host if request.client else None,
        device_id=alerta.device_id,
    )
    await session.commit()
    return alerta_to_response(alerta)


@router.get(
    "/{alerta_id}/acciones-correctivas",
    response_model=list[AccionCorrectivaResponse],
)
async def listar_acciones_correctivas(
    alerta_id: UUID,
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
) -> list[AccionCorrectivaResponse]:
    """HU-23 criterio 4: cronología de atención de una alerta (decisión,
    acción, justificación, responsable y timestamp), incluidas las
    rectificaciones (HU-28) — de solo lectura, sin permitir modificarla."""
    use_case = ConsultarAccionesCorrectivasUseCase(
        SQLAlchemyAlertaRepository(session), SQLAlchemyAccionCorrectivaRepository(session)
    )
    try:
        acciones = await use_case.execute(alerta_id)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [
        AccionCorrectivaResponse(
            id=a.id,
            alert_id=a.alert_id,
            usuario_id=a.usuario_id,
            descripcion=a.descripcion,
            created_at=a.created_at,
            corrige_accion_id=a.corrige_accion_id,
        )
        for a in acciones
    ]


@router.post(
    "/{alerta_id}/acciones-correctivas",
    response_model=AccionCorrectivaResponse,
    status_code=status.HTTP_201_CREATED,
)
async def registrar_accion_correctiva(
    alerta_id: UUID,
    body: AccionCorrectivaCreateRequest,
    session: DbSessionDep,
    request: Request,
    usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO)),
) -> AccionCorrectivaResponse:
    accion_repository = SQLAlchemyAccionCorrectivaRepository(session)
    alerta_repository = SQLAlchemyAlertaRepository(session)
    trazabilidad_repository = SQLAlchemyTrazabilidadRepository(session)
    use_case = RegistrarAccionCorrectivaUseCase(accion_repository, alerta_repository, trazabilidad_repository)
    try:
        accion = await use_case.execute(alerta_id, usuario.id, body.descripcion)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        # HU-27 Escenario 2: la alerta ya no está vigente (otro usuario la
        # atendió primero) — 409, no un 500 ni una sobrescritura silenciosa.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    auditoria_repository = SQLAlchemyAuditLogRepository(session)
    # La alerta ya cambió de estado (ATENDIDA) dentro del use case; se relee
    # solo para obtener su device_id y encadenar este evento en la cadena de
    # ESE dispositivo (HU-25/HU-28), no en la cadena de sistema.
    alerta_para_auditoria = await alerta_repository.obtener_por_id(alerta_id)
    await AuditarAccionCriticaUseCase(
        auditoria_repository, trazabilidad_repository
    ).execute(
        usuario_id=usuario.id,
        accion="REGISTRAR_ACCION_CORRECTIVA",
        recurso=f"alertas/{alerta_id}/acciones-correctivas/{accion.id}",
        detalle={"descripcion": body.descripcion},
        ip_origen=request.client.host if request.client else None,
        device_id=alerta_para_auditoria.device_id if alerta_para_auditoria else None,
    )
    await session.commit()
    return AccionCorrectivaResponse(
        id=accion.id,
        alert_id=accion.alert_id,
        usuario_id=accion.usuario_id,
        descripcion=accion.descripcion,
        created_at=accion.created_at,
        corrige_accion_id=accion.corrige_accion_id,
    )


@router.post(
    "/acciones-correctivas/{accion_id}/rectificar",
    response_model=AccionCorrectivaResponse,
    status_code=status.HTTP_201_CREATED,
)
async def rectificar_accion_correctiva(
    accion_id: UUID,
    body: AccionCorrectivaRectificarRequest,
    session: DbSessionDep,
    request: Request,
    usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO)),
) -> AccionCorrectivaResponse:
    """HU-28: corrige una justificación previamente registrada creando un
    nuevo evento que referencia a la anterior, sin sobrescribirla."""
    accion_repository = SQLAlchemyAccionCorrectivaRepository(session)
    alerta_repository = SQLAlchemyAlertaRepository(session)
    trazabilidad_repository = SQLAlchemyTrazabilidadRepository(session)
    use_case = CorregirAccionCorrectivaUseCase(accion_repository, alerta_repository, trazabilidad_repository)
    try:
        accion = await use_case.execute(accion_id, usuario.id, body.descripcion)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    alerta_para_auditoria = await alerta_repository.obtener_por_id(accion.alert_id)
    auditoria_repository = SQLAlchemyAuditLogRepository(session)
    await AuditarAccionCriticaUseCase(auditoria_repository, trazabilidad_repository).execute(
        usuario_id=usuario.id,
        accion="RECTIFICAR_ACCION_CORRECTIVA",
        recurso=f"alertas/acciones-correctivas/{accion_id}/rectificar",
        detalle={"descripcion": body.descripcion, "corrige_accion_id": str(accion_id)},
        ip_origen=request.client.host if request.client else None,
        device_id=alerta_para_auditoria.device_id if alerta_para_auditoria else None,
    )
    await session.commit()
    return AccionCorrectivaResponse(
        id=accion.id,
        alert_id=accion.alert_id,
        usuario_id=accion.usuario_id,
        descripcion=accion.descripcion,
        created_at=accion.created_at,
        corrige_accion_id=accion.corrige_accion_id,
    )
