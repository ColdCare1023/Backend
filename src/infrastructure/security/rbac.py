from src.domain.exceptions import PermisoDenegadoError
from src.domain.value_objects.rol import Rol


def verificar_permiso(rol_actual: Rol, roles_permitidos: tuple[Rol, ...]) -> None:
    """Aplica RBAC bajo el principio de mínimo privilegio.

    administrador tiene acceso implícito a todo; los demás roles requieren
    pertenecer explícitamente a roles_permitidos.
    """
    if rol_actual == Rol.ADMINISTRADOR:
        return
    if rol_actual not in roles_permitidos:
        raise PermisoDenegadoError(
            f"El rol '{rol_actual.value}' no tiene permiso para esta acción"
        )
