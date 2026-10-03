from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.gestionar_checklist_bpa import (
    ConsultarChecklistBPAUseCase,
    RegistrarChecklistBPAUseCase,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.checklist_bpa import ITEMS_CHECKLIST_BPA, ChecklistBPA
from src.domain.exceptions import DomainError
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.checklist_repository import (
    SQLAlchemyChecklistRepository,
)
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.schemas import ChecklistBPARequest, ChecklistBPAResponse

router = APIRouter(prefix="/api/checklist-bpa", tags=["checklist-bpa"])

# El checklist BPA es una declaración de cumplimiento del responsable técnico:
# lo firma el FARMACÉUTICO. El ADMINISTRADOR accede para supervisión.
_roles_checklist = require_roles(Rol.FARMACEUTICO, Rol.ADMINISTRADOR)


def _to_response(checklist: ChecklistBPA) -> ChecklistBPAResponse:
    return ChecklistBPAResponse(
        id=checklist.id,
        usuario_id=checklist.usuario_id,
        fecha=checklist.fecha,
        observaciones=checklist.observaciones,
        total_conformes=checklist.total_conformes(),
        conforme=checklist.es_conforme(),
        created_at=checklist.created_at,
        updated_at=checklist.updated_at,
        **checklist.items(),
    )


@router.post("", response_model=ChecklistBPAResponse, status_code=status.HTTP_201_CREATED)
async def registrar_checklist(
    body: ChecklistBPARequest,
    session: DbSessionDep,
    request: Request,
    usuario=Depends(_roles_checklist),
) -> ChecklistBPAResponse:
    """HU-37: guarda (o corrige) el checklist BPA del día y lo ancla a la
    cadena de trazabilidad SHA-256."""
    use_case = RegistrarChecklistBPAUseCase(
        SQLAlchemyChecklistRepository(session),
        RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)),
    )
    checklist = ChecklistBPA(
        usuario_id=usuario.id,
        fecha=body.fecha,
        observaciones=body.observaciones,
        **{clave: getattr(body, clave) for clave in ITEMS_CHECKLIST_BPA},
    )
    try:
        guardado = await use_case.execute(checklist)
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=usuario.id,
        accion="CHECKLIST_BPA_REGISTRADO",
        recurso=f"checklist-bpa/{body.fecha}",
        detalle={"fecha": body.fecha, "conforme": guardado.es_conforme()},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    return _to_response(guardado)


@router.get("", response_model=ChecklistBPAResponse | None)
async def obtener_checklist(
    session: DbSessionDep,
    usuario=Depends(_roles_checklist),
    fecha: str | None = None,
) -> ChecklistBPAResponse | None:
    """Checklist de una fecha concreta; sin `fecha`, el del día de hoy.

    Devuelve `null` (no 404) cuando aún no se registró: para el frontend
    "todavía no verificado hoy" es un estado normal, no un error."""
    use_case = ConsultarChecklistBPAUseCase(SQLAlchemyChecklistRepository(session))
    objetivo = fecha or datetime.now(tz=timezone.utc).date().isoformat()
    checklist = await use_case.obtener_del_dia(usuario.id, objetivo)
    return _to_response(checklist) if checklist else None


@router.get("/historial", response_model=list[ChecklistBPAResponse])
async def listar_historial(
    session: DbSessionDep,
    usuario=Depends(_roles_checklist),
    limite: int = Query(default=50, ge=1, le=365),
    offset: int = Query(default=0, ge=0),
) -> list[ChecklistBPAResponse]:
    use_case = ConsultarChecklistBPAUseCase(SQLAlchemyChecklistRepository(session))
    historial = await use_case.listar_historial(usuario.id, limite, offset)
    return [_to_response(c) for c in historial]
