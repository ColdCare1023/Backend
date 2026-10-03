from datetime import datetime, timezone
from uuid import UUID

from src.domain.entities.usuario import Usuario
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_usuario_repository import IUsuarioRepository
from src.domain.value_objects.rol import Rol
from src.infrastructure.security.password_hasher import hash_password


class CrearUsuarioUseCase:
    """RF-17: gestión de usuarios y roles (RBAC) restringida al administrador."""

    def __init__(self, usuario_repository: IUsuarioRepository) -> None:
        self._usuario_repository = usuario_repository

    async def execute(self, nombre: str, email: str, password: str, rol: Rol) -> Usuario:
        existente = await self._usuario_repository.obtener_por_email(email)
        if existente is not None:
            raise DomainError(f"Ya existe un usuario con el email {email}")

        usuario = Usuario(nombre=nombre, email=email, password_hash=hash_password(password), rol=rol)
        return await self._usuario_repository.agregar(usuario)


class ListarUsuariosUseCase:
    def __init__(self, usuario_repository: IUsuarioRepository) -> None:
        self._usuario_repository = usuario_repository

    async def execute(self) -> list[Usuario]:
        return await self._usuario_repository.listar()


class CambiarRolUsuarioUseCase:
    """HU-41: asignar/modificar el rol de un usuario existente.

    Antes de esta historia no existía ningún caso de uso para esto —
    `gestionar_usuarios.py` solo cubría crear/listar/desactivar, así que la
    matriz RBAC no tenía forma real de otorgar o revocar un rol tras el alta.
    """

    def __init__(self, usuario_repository: IUsuarioRepository) -> None:
        self._usuario_repository = usuario_repository

    async def execute(self, usuario_id: UUID, nuevo_rol: Rol) -> Usuario:
        usuario = await self._usuario_repository.obtener_por_id(usuario_id)
        if usuario is None:
            raise RecursoNoEncontradoError(f"Usuario {usuario_id} no encontrado")
        if not usuario.is_active:
            raise DomainError("No se puede cambiar el rol de un usuario desactivado")
        usuario.rol = nuevo_rol
        return await self._usuario_repository.actualizar(usuario)


_MOTIVOS_DESACTIVACION = frozenset({"renuncia", "despido", "jubilacion", "otros"})


class DesactivarUsuarioUseCase:
    """HU-45: desactiva (y anonimiza para reportes/UI) un usuario sin borrar
    su histórico auditable — audit_logs y traceability_records permanecen
    intactos e íntegros; solo se oculta la identidad en la capa de presentación
    (ver mappers.py) cuando anonymized_for_gdpr=True."""

    def __init__(self, usuario_repository: IUsuarioRepository) -> None:
        self._usuario_repository = usuario_repository

    async def execute(self, usuario_id: UUID, motivo: str, admin_id: UUID) -> Usuario:
        if motivo not in _MOTIVOS_DESACTIVACION:
            raise DomainError(f"Motivo de desactivación inválido: {motivo}")
        usuario = await self._usuario_repository.obtener_por_id(usuario_id)
        if usuario is None:
            raise RecursoNoEncontradoError(f"Usuario {usuario_id} no encontrado")
        if not usuario.is_active:
            raise DomainError("El usuario ya está desactivado")

        usuario.is_active = False
        usuario.motivo_desactivacion = motivo
        usuario.desactivado_en = datetime.now(tz=timezone.utc)
        usuario.desactivado_por = admin_id
        usuario.anonymized_for_gdpr = True
        return await self._usuario_repository.actualizar(usuario)
