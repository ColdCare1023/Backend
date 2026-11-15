"""HU-54: tareas periódicas del proceso (una sola réplica y un solo worker, ver
README «Topología»).

`VigilanteSinDatos` revisa cada `SIN_DATOS_PERIODO_SEGUNDOS` qué nodos llevan
más de N x su intervalo sin reportar, abre o cierra el incidente persistente,
entrega el aviso al despachador DESPUÉS del COMMIT y lo publica por SSE.

`VigilanteRespaldos` (HU-29 esc. 3) revisa el resultado del respaldo
programado y deja una alerta crítica en el log del backend si falló.
"""

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.vigilar_sin_datos import CambioIncidente, VigilarSinDatosUseCase
from src.infrastructure.database.models import AuditLogModel
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


# HU-29 esc. 3: acciones que deja scripts/respaldo_postgres.py en audit_logs.
RESPALDO_COMPLETADO = "RESPALDO_COMPLETADO"
RESPALDO_FALLIDO = "RESPALDO_FALLIDO"


class VigilanteRespaldos:
    """HU-29 esc. 3: «cuando el servidor detecta el error de la tarea
    programada, se registra una alerta operativa crítica en el log del
    backend».

    El respaldo corre en un servicio cron aparte (su propio log ya tiene el
    CRITICAL). Este vigilante lee su resultado en `audit_logs` y deja la
    alerta CRÍTICA también en el log del backend:

    - `fallido`: el último RESPALDO_FALLIDO es posterior al último
      RESPALDO_COMPLETADO (una vez por fallo);
    - `vencido`: con `max_antiguedad_horas` > 0, no hay ningún
      RESPALDO_COMPLETADO en ese plazo, p. ej. porque el cron no corrió (una vez
      por episodio).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        periodo_segundos: float,
        max_antiguedad_horas: int,
    ) -> None:
        self._session_factory = session_factory
        self._periodo = periodo_segundos
        self._max_antiguedad_horas = max_antiguedad_horas
        self._tarea: asyncio.Task | None = None
        self._avisado: tuple | None = None
        self.estado: str | None = None
        self.ultimo_completado: datetime | None = None
        self.ultima_pasada: datetime | None = None

    @property
    def activo(self) -> bool:
        return self._tarea is not None and not self._tarea.done()

    async def _ultimo(self, session: AsyncSession, accion: str) -> datetime | None:
        valor = (
            await session.execute(select(func.max(AuditLogModel.created_at)).where(AuditLogModel.accion == accion))
        ).scalar()
        if valor is not None and valor.tzinfo is None:
            valor = valor.replace(tzinfo=timezone.utc)
        return valor

    async def ejecutar_una_vez(self, ahora: datetime | None = None) -> str | None:
        """Estado tras la pasada: None (al día o sin nada que vigilar),
        `fallido` o `vencido`."""
        ahora = ahora or datetime.now(tz=timezone.utc)
        async with self._session_factory() as session:
            completado = await self._ultimo(session, RESPALDO_COMPLETADO)
            fallido = await self._ultimo(session, RESPALDO_FALLIDO)
        self.ultimo_completado = completado
        self.ultima_pasada = ahora
        if fallido is not None and (completado is None or fallido > completado):
            self.estado = "fallido"
            clave = ("fallido", fallido)
            if self._avisado != clave:
                self._avisado = clave
                logger.critical(
                    "ALERTA OPERATIVA: el último respaldo programado de PostgreSQL falló (%s) y no hay uno "
                    "correcto posterior. Revise el servicio cron de respaldo (HU-29).", fallido.isoformat(),
                )
            return self.estado
        if self._max_antiguedad_horas > 0:
            limite = timedelta(hours=self._max_antiguedad_horas)
            if completado is None or ahora - completado > limite:
                self.estado = "vencido"
                clave = ("vencido", completado)
                if self._avisado != clave:
                    self._avisado = clave
                    logger.critical(
                        "ALERTA OPERATIVA: no hay ningún respaldo programado completado en las últimas %d h "
                        "(último: %s). ¿Corre el servicio cron de respaldo? (HU-29)",
                        self._max_antiguedad_horas, completado.isoformat() if completado else "ninguno",
                    )
                return self.estado
        self.estado = None
        self._avisado = None
        return None

    async def _bucle(self) -> None:
        while True:
            try:
                await self.ejecutar_una_vez()
            except Exception:
                logger.exception("Falló la pasada de vigilancia de respaldos.")
            await asyncio.sleep(self._periodo)

    def iniciar(self) -> None:
        if not self.activo:
            self._tarea = asyncio.create_task(self._bucle(), name="vigilante-respaldos")

    async def detener(self) -> None:
        if self._tarea is None:
            return
        self._tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._tarea
        self._tarea = None
