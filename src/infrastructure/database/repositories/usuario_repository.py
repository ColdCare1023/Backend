from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.domain.entities.usuario import Usuario
from src.domain.repositories.i_usuario_repository import IUsuarioRepository
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import RoleModel, UserModel


def _to_entity(model: UserModel) -> Usuario:
    return Usuario(
        id=model.id,
        nombre=model.nombre,
        email=model.email,
        password_hash=model.password_hash,
        rol=Rol(model.rol.nombre),
        privacy_accepted=model.privacy_accepted,
        privacy_accepted_at=model.privacy_accepted_at,
        privacy_version_accepted=model.privacy_version_accepted,
        is_active=model.is_active,
        motivo_desactivacion=model.motivo_desactivacion,
        desactivado_en=model.desactivado_en,
        desactivado_por=model.desactivado_por,
        anonymized_for_gdpr=model.anonymized_for_gdpr,
    )


class SQLAlchemyUsuarioRepository(IUsuarioRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _obtener_rol_model(self, rol: Rol) -> RoleModel:
        stmt = select(RoleModel).where(RoleModel.nombre == rol.value)
        result = await self._session.execute(stmt)
        rol_model = result.scalar_one_or_none()
        if rol_model is None:
            rol_model = RoleModel(nombre=rol.value)
            self._session.add(rol_model)
            await self._session.flush()
        return rol_model

    async def agregar(self, usuario: Usuario) -> Usuario:
        rol_model = await self._obtener_rol_model(usuario.rol)
        # Se persisten TODOS los campos de la entidad, no solo los de
        # identificación. Antes se omitían los de privacidad (HU-44) y ciclo de
        # vida (HU-45), de modo que la fila la rellenaban los `default` del
        # modelo: un usuario creado como inactivo o con consentimiento ya
        # registrado quedaba guardado activo y sin ese consentimiento, en
        # silencio y sin que `agregar` devolviera nada distinto.
        model = UserModel(
            nombre=usuario.nombre,
            email=usuario.email,
            password_hash=usuario.password_hash,
            rol_id=rol_model.id,
            privacy_accepted=usuario.privacy_accepted,
            privacy_accepted_at=usuario.privacy_accepted_at,
            privacy_version_accepted=usuario.privacy_version_accepted,
            is_active=usuario.is_active,
            motivo_desactivacion=usuario.motivo_desactivacion,
            desactivado_en=usuario.desactivado_en,
            desactivado_por=usuario.desactivado_por,
            anonymized_for_gdpr=usuario.anonymized_for_gdpr,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model, attribute_names=["rol"])
        return _to_entity(model)

    async def obtener_por_email(self, email: str) -> Usuario | None:
        stmt = select(UserModel).options(joinedload(UserModel.rol)).where(UserModel.email == email)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def obtener_por_id(self, usuario_id: UUID) -> Usuario | None:
        stmt = select(UserModel).options(joinedload(UserModel.rol)).where(UserModel.id == usuario_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_entity(model) if model else None

    async def listar(self) -> list[Usuario]:
        stmt = select(UserModel).options(joinedload(UserModel.rol))
        result = await self._session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().unique().all()]

    async def actualizar(self, usuario: Usuario) -> Usuario:
        model = await self._session.get(UserModel, usuario.id)
        if model is None:
            raise ValueError(f"Usuario {usuario.id} no encontrado")
        rol_model = await self._obtener_rol_model(usuario.rol)
        model.nombre = usuario.nombre
        model.email = usuario.email
        model.password_hash = usuario.password_hash
        model.rol_id = rol_model.id
        model.privacy_accepted = usuario.privacy_accepted
        model.privacy_accepted_at = usuario.privacy_accepted_at
        model.privacy_version_accepted = usuario.privacy_version_accepted
        model.is_active = usuario.is_active
        model.motivo_desactivacion = usuario.motivo_desactivacion
        model.desactivado_en = usuario.desactivado_en
        model.desactivado_por = usuario.desactivado_por
        model.anonymized_for_gdpr = usuario.anonymized_for_gdpr
        await self._session.flush()
        await self._session.refresh(model, attribute_names=["rol"])
        return _to_entity(model)
