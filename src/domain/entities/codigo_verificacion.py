"""Código de verificación de un solo uso enviado por correo."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID


class PropositoCodigo(StrEnum):
    LOGIN = "login"
    RECUPERACION = "recuperacion"
    ACTIVACION = "activacion"


# Un código de recuperación o de activación sirve para lo mismo (elegir la
# contraseña): comparten el tope de fallos por cuenta y emitir uno anula el otro.
PROPOSITOS_PASSWORD: tuple[PropositoCodigo, ...] = (PropositoCodigo.RECUPERACION, PropositoCodigo.ACTIVACION)


def grupo_de(proposito: PropositoCodigo) -> tuple[PropositoCodigo, ...]:
    return PROPOSITOS_PASSWORD if proposito in PROPOSITOS_PASSWORD else (proposito,)


def a_utc(valor: datetime) -> datetime:
    """SQLite devuelve datetimes naive aunque la columna sea timezone-aware."""
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


_a_utc = a_utc


@dataclass(slots=True)
class CodigoVerificacion:
    usuario_id: UUID
    proposito: PropositoCodigo
    # HMAC-SHA256 (hex) del código: el valor solo viaja en el correo.
    codigo_hash: str
    expira_en: datetime
    id: UUID | None = None
    # SHA-256 (hex) del desafío opaco que el login entrega al navegador.
    desafio_hash: str | None = None
    intentos: int = 0
    # Marca de consumo, de quema por intentos o de anulación (código reemplazado).
    usado_en: datetime | None = None
    # Último envío: el reenvío lo mueve y la espera entre envíos se mide desde aquí.
    creado_en: datetime | None = None
    # Emisión original, inmutable: acota la vida del desafío y se compara con
    # la invalidación de sesiones del usuario.
    emitido_en: datetime | None = None
    ip: str | None = None

    def caducado(self, ahora: datetime) -> bool:
        return _a_utc(self.expira_en) <= _a_utc(ahora)

    def utilizable(self, ahora: datetime, max_intentos: int) -> bool:
        """Sin usar, sin caducar y con intentos disponibles."""
        return self.usado_en is None and not self.caducado(ahora) and self.intentos < max_intentos

    def emitido_antes_de(self, instante: datetime | None) -> bool:
        """True si el código se emitió antes de `instante` (p. ej. un cambio de
        contraseña o una invalidación de sesiones)."""
        if instante is None:
            return False
        origen = self.emitido_en or self.creado_en
        return origen is None or _a_utc(origen) < _a_utc(instante)

    def segundos_desde_emision(self, ahora: datetime) -> float:
        if self.creado_en is None:
            return float("inf")
        return (_a_utc(ahora) - _a_utc(self.creado_en)).total_seconds()
