from typing import NoReturn
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
from src.domain.exceptions import DomainError, OperacionRbacRechazadaError, RecursoNoEncontradoError
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.security.privacidad import enmascarar_email
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.mappers import nombre_visible
from src.interface.api.schemas import (
    CambiarRolUsuarioRequest,
    DesactivarUsuarioRequest,
    UsuarioCreateRequest,
    UsuarioResponse,
)

EMAIL_ANONIMIZADO = "—"

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])


def _to_response(usuario) -> UsuarioResponse:
    # HU-41/HU-45 (D7): un usuario desactivado y anonimizado no se identifica en
    # la interfaz; su historial auditable sigue intacto en la base de datos.
    anonimizado = bool(getattr(usuario, "anonymized_for_gdpr", False))
    return UsuarioResponse(
        id=usuario.id,
        nombre=nombre_visible(usuario),
        email=EMAIL_ANONIMIZADO if anonimizado else usuario.email,
        rol=usuario.rol,
        is_active=usuario.is_active,
        motivo_desactivacion=usuario.motivo_desactivacion,
        desactivado_en=usuario.desactivado_en,
    )


async def _rechazar_operacion_rbac(
    session, request: Request, admin_id: UUID, operacion: str, usuario_id: UUID,
    exc: OperacionRbacRechazadaError,
) -> NoReturn:
    """HU-41: el intento rechazado también es evidencia (quién intentó qué)."""
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin_id,
        accion="RBAC_OPERACION_RECHAZADA",
        recurso=f"usuarios/{usuario_id}",
        detalle={"operacion": operacion, "motivo": exc.motivo},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


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
        # Ley 29733: audit_logs es inmutable; nunca el correo completo.
        detalle={"email": enmascarar_email(usuario.email), "rol": usuario.rol.value},
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
    except OperacionRbacRechazadaError as exc:
        await _rechazar_operacion_rbac(session, request, admin.id, "DESACTIVAR_USUARIO", usuario_id, exc)
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
    # HU-41 (P9): su stream SSE abierto se cierra en la siguiente vuelta.
    request.app.state.usuarios_desactivados.add(usuario_id)
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
        usuario = await use_case.execute(usuario_id, body.rol, admin.id)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OperacionRbacRechazadaError as exc:
        await _rechazar_operacion_rbac(session, request, admin.id, "CAMBIAR_ROL_USUARIO", usuario_id, exc)
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
