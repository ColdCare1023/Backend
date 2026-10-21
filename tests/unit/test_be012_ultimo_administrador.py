"""BE-012: ninguna operación deja al sistema sin administrador activo."""

from uuid import uuid4

import pytest

from src.application.use_cases.gestionar_usuarios import CambiarRolUsuarioUseCase, DesactivarUsuarioUseCase
from src.domain.entities.usuario import Usuario
from src.domain.exceptions import DomainError
from src.domain.value_objects.rol import Rol


class _Repo:
    def __init__(self, usuario: Usuario, admins_activos: int) -> None:
        self._usuario = usuario
        self._admins = admins_activos

    async def obtener_por_id(self, _id):
        return self._usuario

    async def contar_administradores_activos(self) -> int:
        return self._admins

    async def actualizar(self, usuario):
        return usuario


def _admin() -> Usuario:
    return Usuario(nombre="A", email="a@x.org", password_hash="h", rol=Rol.ADMINISTRADOR, id=uuid4())


@pytest.mark.asyncio
async def test_no_se_degrada_al_ultimo_administrador():
    admin = _admin()
    with pytest.raises(DomainError, match="al menos un administrador"):
        await CambiarRolUsuarioUseCase(_Repo(admin, 1)).execute(admin.id, Rol.TECNICO, actor_id=uuid4())


@pytest.mark.asyncio
async def test_no_se_desactiva_al_ultimo_administrador():
    admin = _admin()
    with pytest.raises(DomainError, match="al menos un administrador"):
        await DesactivarUsuarioUseCase(_Repo(admin, 1)).execute(admin.id, "otros", uuid4())


@pytest.mark.asyncio
async def test_con_otro_administrador_si_se_permite():
    admin = _admin()
    resultado = await CambiarRolUsuarioUseCase(_Repo(admin, 2)).execute(admin.id, Rol.TECNICO, actor_id=uuid4())
    assert resultado.rol == Rol.TECNICO
