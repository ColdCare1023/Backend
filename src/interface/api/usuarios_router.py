from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.gestionar_usuarios import (
    CambiarRolUsuarioUseCase,
    CrearUsuarioUseCase,
    DesactivarUsuarioUseCase,
    ListarUsuariosUseCase,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.schemas import (
    CambiarRolUsuarioRequest,
    DesactivarUsuarioRequest,
    UsuarioCreateRequest,
    UsuarioResponse,
)

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])


def _to_response(usuario) -> UsuarioResponse:
    return UsuarioResponse(
        id=usuario.id,
        nombre=usuario.nombre,
        email=usuario.email,
        rol=usuario.rol,
        is_active=usuario.is_active,
        motivo_desactivacion=usuario.motivo_desactivacion,
        desactivado_en=usuario.desactivado_en,
    )


@router.post("", response_model=UsuarioResponse, status_code=status.HTTP_201_CREATED)
async def crear_usuario(
    body: UsuarioCreateRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> UsuarioResponse:
    repositorio = SQLAlchemyUsuarioRepository(session)
    use_case = CrearUsuarioUseCase(repositorio)
    try:
        usuario = await use_case.execute(body.nombre, body.email, body.password, body.rol)
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    auditoria_repository = SQLAlchemyAuditLogRepository(session)
    await AuditarAccionCriticaUseCase(
        auditoria_repository, SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="CREAR_USUARIO",
        recurso=f"usuarios/{usuario.id}",
        detalle={"email": usuario.email, "rol": usuario.rol.value},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    return _to_response(usuario)


@router.get("", response_model=list[UsuarioResponse])
async def listar_usuarios(
    session: DbSessionDep,
    _admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> list[UsuarioResponse]:
    repositorio = SQLAlchemyUsuarioRepository(session)
    use_case = ListarUsuariosUseCase(repositorio)
    usuarios = await use_case.execute()
    return [_to_response(u) for u in usuarios]


@router.patch("/{usuario_id}/desactivar", response_model=UsuarioResponse)
async def desactivar_usuario(
    usuario_id: UUID,
    body: DesactivarUsuarioRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> UsuarioResponse:
    """HU-45: desactivación/anonimización (derecho al olvido, Ley N.° 29733)."""
    repositorio = SQLAlchemyUsuarioRepository(session)
    use_case = DesactivarUsuarioUseCase(repositorio)
    try:
        usuario = await use_case.execute(usuario_id, body.motivo, admin.id)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    ip = request.client.host if request.client else None
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="DESACTIVAR_USUARIO",
        recurso=f"usuarios/{usuario_id}",
        detalle={"motivo": body.motivo},
        ip_origen=ip,
    )
    await RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)).execute(
        tipo_evento="DESACTIVACION_USUARIO",
        payload={"usuario_desactivado_id": str(usuario_id), "motivo": body.motivo, "ip_origen_admin": ip},
        usuario_id=admin.id,
    )
    await session.commit()
    return _to_response(usuario)


@router.patch("/{usuario_id}/rol", response_model=UsuarioResponse)
async def cambiar_rol(
    usuario_id: UUID,
    body: CambiarRolUsuarioRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> UsuarioResponse:
    """HU-41: asignar/modificar el rol de un usuario existente respetando la
    matriz ADMINISTRADOR/FARMACEUTICO/TECNICO/AUDITOR. Antes de esta historia
    no existía ningún endpoint para esto tras el alta inicial."""
    repositorio = SQLAlchemyUsuarioRepository(session)
    use_case = CambiarRolUsuarioUseCase(repositorio)
    rol_anterior = None
    try:
        objetivo = await repositorio.obtener_por_id(usuario_id)
        if objetivo is not None:
            rol_anterior = objetivo.rol.value
        usuario = await use_case.execute(usuario_id, body.rol)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    ip = request.client.host if request.client else None
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="CAMBIAR_ROL_USUARIO",
        recurso=f"usuarios/{usuario_id}",
        detalle={"rol_anterior": rol_anterior, "rol_nuevo": body.rol.value},
        ip_origen=ip,
    )
    await session.commit()
    return _to_response(usuario)
