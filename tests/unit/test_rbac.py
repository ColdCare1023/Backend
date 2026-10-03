import pytest

from src.domain.exceptions import PermisoDenegadoError
from src.domain.value_objects.rol import Rol
from src.infrastructure.security.rbac import verificar_permiso


def test_administrador_tiene_acceso_a_todo():
    verificar_permiso(Rol.ADMINISTRADOR, (Rol.TECNICO,))
    verificar_permiso(Rol.ADMINISTRADOR, ())


def test_rol_permitido_no_lanza_error():
    verificar_permiso(Rol.FARMACEUTICO, (Rol.FARMACEUTICO, Rol.TECNICO))


def test_rol_no_permitido_lanza_permiso_denegado():
    with pytest.raises(PermisoDenegadoError):
        verificar_permiso(Rol.TECNICO, (Rol.ADMINISTRADOR,))


def test_rol_no_permitido_sin_roles_permitidos_lanza_error():
    with pytest.raises(PermisoDenegadoError):
        verificar_permiso(Rol.FARMACEUTICO, ())
