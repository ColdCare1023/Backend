from enum import StrEnum


class Rol(StrEnum):
    ADMINISTRADOR = "administrador"
    FARMACEUTICO = "farmaceutico"
    TECNICO = "tecnico"
    # HU-41 (backlog de 51 HU finales): "Responsable de auditoría" es el rol
    # narrativo de HU-26, HU-28, HU-37, HU-42, HU-47, HU-49 y HU-50, pero no
    # existía como valor del enum — esas historias no tenían forma de otorgar
    # acceso de solo lectura sin conceder el privilegio implícito total de
    # ADMINISTRADOR (ver verificar_permiso en rbac.py).
    AUDITOR = "auditor"
