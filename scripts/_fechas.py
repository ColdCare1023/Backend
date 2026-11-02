"""Fechas de línea de comandos para los scripts de banco.

asyncpg no acepta texto donde la columna es TIMESTAMPTZ (ni siquiera con CAST):
exige un `datetime`. Estas funciones convierten antes de ejecutar la consulta."""

from datetime import datetime, timezone


def a_datetime(texto: str) -> datetime:
    """ISO 8601 (con «Z» o sin zona) -> datetime con zona. Sin zona se asume UTC."""
    valor = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


def a_datetime_opcional(texto: str | None) -> datetime | None:
    return a_datetime(texto) if texto else None
