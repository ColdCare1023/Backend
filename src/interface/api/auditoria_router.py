from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.schemas import AuditLogResponse

router = APIRouter(prefix="/api/auditoria", tags=["auditoria"])


@router.get("", response_model=list[AuditLogResponse])
async def listar_auditoria(
    session: DbSessionDep,
    # HU-42: "el auditor consulta la bitácora... obtiene una vista de solo
    # lectura" — antes solo ADMINISTRADOR podía leerla, contradiciendo el rol
    # narrativo de la propia historia.
    _usuario=Depends(require_roles(Rol.ADMINISTRADOR, Rol.AUDITOR)),
    limite: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    # HU-42 criterio 4 / HU-50: filtrar por periodo, actor o tipo de acción.
    desde: datetime | None = None,
    hasta: datetime | None = None,
    usuario_id: UUID | None = None,
    accion: str | None = None,
) -> list[AuditLogResponse]:
    repositorio = SQLAlchemyAuditLogRepository(session)
    registros = await repositorio.listar(
        limite=limite, offset=offset, desde=desde, hasta=hasta, usuario_id=usuario_id, accion=accion
    )
    return [AuditLogResponse(**registro) for registro in registros]
