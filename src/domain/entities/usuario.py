from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.domain.value_objects.rol import Rol

PRIVACY_POLICY_VERSION = "1.0"


def _a_utc(valor: datetime) -> datetime:
    """SQLite devuelve datetimes naive aunque la columna sea timezone-aware."""
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


@dataclass(slots=True)
class Usuario:
    nombre: str
    email: str
    password_hash: str
    rol: Rol
    id: UUID | None = None
    # HU-44: consentimiento Ley N.° 29733. BE-014: nunca presunto; lo
    # registra solo POST /api/auth/privacidad/aceptar.
    privacy_accepted: bool = False
    privacy_accepted_at: datetime | None = None
    privacy_version_accepted: str | None = None
    # HU-45: ciclo de vida / derecho al olvido.
    is_active: bool = True
    motivo_desactivacion: str | None = None
    desactivado_en: datetime | None = None
    desactivado_por: UUID | None = None
    anonymized_for_gdpr: bool = False
    # BE-009: los access tokens emitidos antes de este instante no valen.
    tokens_invalidos_antes_de: datetime | None = None

    def tiene_permiso(self, roles_permitidos: tuple[Rol, ...]) -> bool:
        return self.rol in roles_permitidos

    @property
    def requiere_aceptar_privacidad(self) -> bool:
        return not self.privacy_accepted or self.privacy_version_accepted != PRIVACY_POLICY_VERSION

    def invalidar_sesiones(self, cuando: datetime, conservar_mismo_segundo: bool = False) -> None:
        """El claim `iat` del JWT tiene resolución de segundos. Por defecto se
        invalida hasta el segundo siguiente, así que ningún token emitido
        antes del cambio sobrevive. Con `conservar_mismo_segundo` (cambio de
        contraseña) el token que se entrega en esa misma operación vale."""
        instante = _a_utc(cuando)
        base = instante.replace(microsecond=0)
        if not conservar_mismo_segundo and instante != base:
            base += timedelta(seconds=1)
        self.tokens_invalidos_antes_de = base

    def token_vigente(self, emitido_en: datetime | None) -> bool:
        if self.tokens_invalidos_antes_de is None:
            return True
        if emitido_en is None:
            return False
        return _a_utc(emitido_en) >= _a_utc(self.tokens_invalidos_antes_de)
