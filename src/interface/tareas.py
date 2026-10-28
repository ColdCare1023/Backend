"""HU-54: tareas periódicas del proceso (una sola réplica y un solo worker, ver
README «Topología»).

`VigilanteSinDatos` revisa cada `SIN_DATOS_PERIODO_SEGUNDOS` qué nodos llevan
más de N x su intervalo sin reportar, abre o cierra el incidente persistente,
entrega el aviso al despachador DESPUÉS del COMMIT y lo publica por SSE.
"""

import asyncio
import contextlib
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.vigilar_sin_datos import CambioIncidente, VigilarSinDatosUseCase
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.incidente_repository import SQLAlchemyIncidenteRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.notifications.despachador import DespachadorNotificaciones
from src.interface.api.sse_broadcaster import SSEBroadcaster
from src.interface.limitador_auditoria import LIMITE_AUDITORIA, auditar_suprimidos

logger = logging.getLogger("interface.tareas")


def tipo_sse(cambio: CambioIncidente) -> str:
    """`sin_datos` / `sin_datos_resuelto`; HU-44: igual para
    `monitoreo_suspendido` y `excursion_no_verificada`."""
    return cambio.incidente.tipo if cambio.tipo == "abierto" else f"{cambio.incidente.tipo}_resuelto"


class VigilanteSinDatos:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        broadcaster: SSEBroadcaster,
        notificaciones: DespachadorNotificaciones | None,
        periodo_segundos: float,
        n_intervalos: int,
    ) -> None:
        self._session_factory = session_factory
        self._broadcaster = broadcaster
        self._notificaciones = notificaciones
        self._periodo = periodo_segundos
        self._n = n_intervalos
        self._tarea: asyncio.Task | None = None
        self.ultima_pasada: datetime | None = None

    @property
    def activo(self) -> bool:
        return self._tarea is not None and not self._tarea.done()

    async def ejecutar_una_vez(self, ahora: datetime | None = None) -> list[CambioIncidente]:
        async with self._session_factory() as session:
            auditoria = AuditarAccionCriticaUseCase(
                SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
            )
            use_case = VigilarSinDatosUseCase(
                SQLAlchemyDeviceRepository(session),
                SQLAlchemyLecturaRepository(session),
                SQLAlchemyIncidenteRepository(session),
                auditoria,
                n_intervalos=self._n,
            )
            cambios = await use_case.execute(ahora)
            # HU-44: si quien publicaba con el token filtrado deja de hacerlo,
            # lo contado en su última ventana no lo auditaría ningún intento
            # nuevo: se vuelca aquí en cuanto la ventana vence.
            for device_id, suprimidos in LIMITE_AUDITORIA.vencidos().items():
                await auditar_suprimidos(auditoria, device_id, suprimidos, "ventana_vencida")
            await session.commit()
        if self._notificaciones is not None:
            self._notificaciones.encolar_todos(use_case.avisos_pendientes)
        for cambio in cambios:
            await self._broadcaster.publicar(cambio.como_evento_sse(), tipo_sse(cambio))
        self.ultima_pasada = ahora or datetime.now(tz=timezone.utc)
        return cambios

    async def _bucle(self) -> None:
        while True:
            try:
                await self.ejecutar_una_vez()
            except Exception:
                # Una BD caída en una pasada no detiene la vigilancia: la
                # siguiente lo vuelve a intentar.
                logger.exception("Falló la pasada de vigilancia SIN_DATOS.")
            await asyncio.sleep(self._periodo)

    def iniciar(self) -> None:
        if not self.activo:
            self._tarea = asyncio.create_task(self._bucle(), name="vigilante-sin-datos")

    async def detener(self) -> None:
        if self._tarea is None:
            return
        self._tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._tarea
        self._tarea = None
