from datetime import datetime, timezone
from uuid import UUID

from src.domain.entities.usuario import Usuario
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_usuario_repository import IUsuarioRepository
from src.domain.value_objects.rol import Rol
from src.infrastructure.security.password_hasher import hash_password


async def _exigir_otro_administrador(
    repositorio: IUsuarioRepository, usuario: Usuario, nuevo_rol: Rol | None
) -> None:
    """BE-012: ninguna operación deja al sistema sin un administrador activo.
    `nuevo_rol=None` significa que el usuario deja de estar activo."""
    deja_de_ser_admin = usuario.rol == Rol.ADMINISTRADOR and nuevo_rol != Rol.ADMINISTRADOR
    if deja_de_ser_admin and await repositorio.contar_administradores_activos() <= 1:
        raise DomainError("Debe quedar al menos un administrador activo")


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

    async def execute(self, usuario_id: UUID, nuevo_rol: Rol, actor_id: UUID | None = None) -> Usuario:
        # BE-012: la regla vive en el servidor, no solo en la interfaz.
        if actor_id is not None and usuario_id == actor_id:
            raise DomainError("Un administrador no puede modificar el rol de su propia cuenta")
        usuario = await self._usuario_repository.obtener_por_id(usuario_id)
        if usuario is None:
            raise RecursoNoEncontradoError(f"Usuario {usuario_id} no encontrado")
        if not usuario.is_active:
            raise DomainError("No se puede cambiar el rol de un usuario desactivado")
        await _exigir_otro_administrador(self._usuario_repository, usuario, nuevo_rol)
        usuario.rol = nuevo_rol
        # BE-009: el token viaja con el rol anterior; se fuerza un login nuevo.
        usuario.invalidar_sesiones(datetime.now(tz=timezone.utc))
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
        # BE-012: un administrador no se desactiva a sí mismo.
        if usuario_id == admin_id:
            raise DomainError("Un administrador no puede desactivar su propia cuenta")
        usuario = await self._usuario_repository.obtener_por_id(usuario_id)
        if usuario is None:
            raise RecursoNoEncontradoError(f"Usuario {usuario_id} no encontrado")
        if not usuario.is_active:
            raise DomainError("El usuario ya está desactivado")
        await _exigir_otro_administrador(self._usuario_repository, usuario, None)

        usuario.is_active = False
        usuario.motivo_desactivacion = motivo
        usuario.desactivado_en = datetime.now(tz=timezone.utc)
        usuario.desactivado_por = admin_id
        usuario.anonymized_for_gdpr = True
        # BE-009: corta de inmediato toda sesión abierta del usuario.
        usuario.invalidar_sesiones(usuario.desactivado_en)
        return await self._usuario_repository.actualizar(usuario)
