from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from src.domain.entities.codigo_verificacion import CodigoVerificacion, PropositoCodigo


class ICodigoVerificacionRepository(ABC):
    """Códigos de verificación de un solo uso. Toda transición (consumir,
    contar un intento, renovar) es un UPDATE condicional: dos peticiones
    simultáneas no pueden usar el mismo código ni saltarse el límite.

    Los códigos reemplazados o anulados no se borran (quedan con `usado_en`):
    sus intentos fallidos siguen contando en el tope por cuenta hasta que se
    purgan, pasada la ventana."""

    @abstractmethod
    async def emitir(
        self, codigo: CodigoVerificacion, anular: Iterable[PropositoCodigo], purgar_antes_de: datetime
    ) -> CodigoVerificacion:
        """Guarda un código nuevo y anula los vivos del mismo usuario para los
        propósitos de `anular`: solo vale el último que se envió. Serializa las
        emisiones simultáneas del mismo usuario. Borra los códigos ya muertos
        del usuario emitidos antes de `purgar_antes_de`."""

    @abstractmethod
    async def obtener_por_desafio(self, desafio_hash: str, proposito: PropositoCodigo) -> CodigoVerificacion | None: ...

    @abstractmethod
    async def listar_utilizables(
        self, usuario_id: UUID, propositos: Iterable[PropositoCodigo], ahora: datetime, max_intentos: int
    ) -> list[CodigoVerificacion]:
        """Vigentes, del más reciente al más antiguo."""

    @abstractmethod
    async def contar_fallos(self, usuario_id: UUID, propositos: Iterable[PropositoCodigo], desde: datetime) -> int:
        """Intentos fallidos acumulados en los códigos (vivos o no) emitidos desde `desde`."""

    @abstractmethod
    async def registrar_intento_fallido(self, codigo_id: UUID, max_intentos: int, ahora: datetime) -> int:
        """Suma un intento; al llegar al máximo el código queda quemado
        (`usado_en`). Devuelve los intentos acumulados (0 si ya no estaba vigente)."""

    @abstractmethod
    async def consumir(self, codigo_id: UUID, codigo_hash: str, max_intentos: int, ahora: datetime) -> bool:
        """Marca el código como usado solo si sigue vigente y su huella no
        cambió. True si esta llamada fue la que lo consumió."""

    @abstractmethod
    async def renovar(
        self, codigo_id: UUID, codigo_hash: str, expira_en: datetime, ahora: datetime, emitido_antes_de: datetime
    ) -> bool:
        """Reemplaza el código de un registro vigente (reenvío) si se envió
        antes de `emitido_antes_de`. Conserva el desafío, los intentos y `emitido_en`."""

    @abstractmethod
    async def anular_pendientes(
        self, usuario_id: UUID, ahora: datetime, propositos: Iterable[PropositoCodigo] | None = None
    ) -> None:
        """Deja sin efecto los códigos vivos (conserva sus intentos para el tope)."""

    @abstractmethod
    async def reiniciar(self, usuario_id: UUID, propositos: Iterable[PropositoCodigo] | None = None) -> None:
        """Borra el historial de códigos (vivos o no) y con él los fallos acumulados."""
