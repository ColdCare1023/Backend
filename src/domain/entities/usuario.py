from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.domain.value_objects.rol import Rol

PRIVACY_POLICY_VERSION = "1.0"


@dataclass(slots=True)
class Usuario:
    nombre: str
    email: str
    password_hash: str
    rol: Rol
    id: UUID | None = None
    # HU-44: consentimiento Ley N.° 29733.
    privacy_accepted: bool = True
    privacy_accepted_at: datetime | None = None
    privacy_version_accepted: str | None = None
    # HU-45: ciclo de vida / derecho al olvido.
    is_active: bool = True
    motivo_desactivacion: str | None = None
    desactivado_en: datetime | None = None
    desactivado_por: UUID | None = None
    anonymized_for_gdpr: bool = False

    def tiene_permiso(self, roles_permitidos: tuple[Rol, ...]) -> bool:
        return self.rol in roles_permitidos

    @property
    def requiere_aceptar_privacidad(self) -> bool:
        return not self.privacy_accepted or self.privacy_version_accepted != PRIVACY_POLICY_VERSION
