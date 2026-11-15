from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from src.domain.entities.alerta_termica import AlertaTermica
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo


class IAlertaRepository(ABC):
    @abstractmethod
    async def agregar(self, alerta: AlertaTermica) -> AlertaTermica: ...

    @abstractmethod
    async def obtener_por_id(self, alerta_id: UUID, refrescar: bool = False) -> AlertaTermica | None:
        """`refrescar`: releer de la base aunque la sesión ya tenga la fila
        (tras un UPDATE atómico como `reconocer_si_pendiente`)."""
        ...

    @abstractmethod
    async def listar(
        self,
        device_id: str | None = None,
        revisada: bool | None = None,
        estado: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[AlertaTermica]: ...

    @abstractmethod
    async def actualizar(self, alerta: AlertaTermica) -> AlertaTermica: ...

    @abstractmethod
    async def marcar_atendida_si_no_atendida(self, alerta_id: UUID, timestamp: datetime) -> bool:
        """HU-27 Escenario 2 y HU-23 Escenario 3: transición atómica
        RECONOCIDA -> ATENDIDA a nivel de base de datos (UPDATE ... WHERE
        estado = 'reconocida'), no un patrón leer-modificar-escribir en
        memoria. Una alerta PENDIENTE no puede saltar a ATENDIDA sin que conste
        quién la reconoció. Dos solicitudes casi simultáneas sobre la misma
        alerta deben resolverse de forma que solo una gane — la que pierde
        recibe False y NO debe registrar su acción correctiva. Devuelve True si
        esta llamada fue la que transicionó la alerta. (El nombre se conserva
        por compatibilidad.)"""
        ...

    async def reconocer_si_pendiente(self, alerta_id: UUID, usuario_id: UUID, timestamp: datetime) -> bool:
        """HU-23 Escenario 1: PENDIENTE -> RECONOCIDA, registrando quién y
        cuándo. Devuelve False si la alerta no existe o ya no está PENDIENTE.
        Las implementaciones sobre base de datos deben hacerlo de forma atómica
        (UPDATE ... WHERE estado = 'pendiente'); esta versión por defecto sirve
        para los repositorios en memoria."""
        alerta = await self.obtener_por_id(alerta_id)
        if alerta is None or alerta.estado != EstadoAlerta.PENDIENTE:
            return False
        alerta.reconocer(usuario_id, timestamp)
        await self.actualizar(alerta)
        return True

    @abstractmethod
    async def obtener_episodio_abierto(self, device_id: str) -> AlertaTermica | None:
        """AIV-02: el episodio de alerta actualmente abierto para el
        dispositivo (a lo sumo uno en la práctica, dado que un dispositivo
        tiene un único nivel_riesgo por lectura)."""
        ...

    @abstractmethod
    async def obtener_ultimo_cerrado(
        self, device_id: str, nivel_riesgo: NivelRiesgo
    ) -> AlertaTermica | None:
        """AIV-02: el episodio cerrado más reciente de este dispositivo y
        tipo de riesgo, para decidir si reabrirlo dentro de la ventana de
        cooldown en vez de crear una alerta nueva."""
        ...

    async def contar_por_nivel(
        self, device_id: str | None, desde: datetime | None, hasta: datetime | None
    ) -> dict[str, int] | None:
        """HU-38 esc. 3: episodios de alerta por nivel en TODO el periodo (por
        `created_at`, como `listar`). None si el repositorio no lo soporta."""
        return None

    async def listar_por_nivel(
        self,
        nivel_riesgo: str,
        device_id: str | None,
        desde: datetime | None,
        hasta: datetime | None,
        limite: int = 10_000,
    ) -> list[AlertaTermica]:
        """HU-38 esc. 3: alertas de un nivel (p. ej. todas las excursiones
        críticas) aunque el listado general del periodo se haya recortado."""
        alertas = await self.listar(device_id=device_id, desde=desde, hasta=hasta, limite=limite)
        return [a for a in alertas if a.nivel_riesgo is not None and a.nivel_riesgo.value == nivel_riesgo]

