"""HU-53: entrega del aviso por correo fuera de la ingesta y después del COMMIT.

Flujo (D2 de la especificación):

    lectura ─> RegistrarLecturaTermicaUseCase ─> avisos_pendientes
                       └─ session.commit() ─> despachador.encolar(aviso)
                                                   │ asyncio.Queue
                                                   ▼
                                      worker (una tarea, lifespan)
        1. alerta ya notificada (notificada_en)          → se descarta
        2. destinatario: responsable del dispositivo, o el de respaldo
        3. transporte.enviar()
        4. éxito → notificada_en + NOTIFICACION_ENVIADA
           fallo → intentos + NOTIFICACION_FALLIDA, y reintento con espera

Garantías:

- **No bloquea la ingesta.** El consumidor MQTT solo hace `put_nowait`; un
  proveedor lento retrasa a lo sumo el siguiente correo, nunca la siguiente
  lectura.
- **Una vez por episodio.** `notificada_en` es persistente: sobrevive al
  reinicio del backend y a la reapertura del episodio dentro de la ventana de
  normalización (mismo id de alerta).
- **Fallos visibles.** Cada intento queda en `audit_logs` y en la cadena del
  dispositivo; un aviso que no salió no pasa por enviado.

Una sola instancia por proceso (D1): se crea en el lifespan de FastAPI.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.generar_alerta import AvisoExcursion
from src.infrastructure.config import Settings
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.notifications.notificacion_service import (
    NotificacionService,
    componer_correo_excursion,
)
from src.infrastructure.notifications.transporte_email import (
    ErrorEnvioCorreo,
    TransporteEmail,
    construir_transporte,
)
from src.infrastructure.security.privacidad import enmascarar_email

logger = logging.getLogger("infrastructure.notificaciones.despachador")

ESPERAS_REINTENTO_SEGUNDOS: tuple[float, ...] = (30, 120, 300)
CAPACIDAD_COLA = 200
# Al arrancar se recuperan avisos de episodios críticos abiertos que la cola en
# memoria perdió con el reinicio; más antiguos que esto ya no son un aviso útil.
VENTANA_RECUPERACION = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class ResultadoEnvio:
    estado: str  # enviado | fallido | ya_notificado | sin_destinatario | alerta_inexistente | deshabilitado
    intentos: int = 0


class DespachadorNotificaciones:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        transporte: TransporteEmail | None = None,
        canales_opcionales: NotificacionService | None = None,
        esperas_reintento: Sequence[float] = ESPERAS_REINTENTO_SEGUNDOS,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._transporte = transporte if transporte is not None else construir_transporte(settings)
        self._canales_opcionales = canales_opcionales or NotificacionService(settings)
        self._esperas = tuple(esperas_reintento)
        self._max_intentos = settings.notificacion_max_intentos
        self._cola: asyncio.Queue[AvisoExcursion] = asyncio.Queue(maxsize=CAPACIDAD_COLA)
        self._worker: asyncio.Task | None = None
        self._reintentos: set[asyncio.Task] = set()

    @property
    def pendientes(self) -> int:
        return self._cola.qsize()

    @property
    def activo(self) -> bool:
        return self._worker is not None and not self._worker.done()

    # ── Entrada ───────────────────────────────────────────────────────────
    def encolar(self, aviso: AvisoExcursion) -> bool:
        """Nunca bloquea ni lanza: lo llama el consumidor MQTT tras el COMMIT."""
        try:
            self._cola.put_nowait(aviso)
            return True
        except asyncio.QueueFull:
            # El barrido del próximo arranque lo recupera (notificada_en IS NULL).
            logger.error("Cola de notificaciones llena; aviso de %s no encolado.", aviso.device_id)
            return False

    def encolar_todos(self, avisos: Sequence[AvisoExcursion]) -> None:
        for aviso in avisos:
            self.encolar(aviso)

    # ── Ciclo de vida ─────────────────────────────────────────────────────
    async def iniciar(self) -> None:
        if self.activo:
            return
        self._worker = asyncio.create_task(self._consumir(), name="despachador-notificaciones")
        try:
            await self.recuperar_pendientes()
        except Exception:  # pragma: no cover - una BD caída al arrancar no impide servir
            logger.exception("No se pudieron recuperar avisos pendientes al arrancar.")

    async def detener(self, timeout: float = 5.0) -> None:
        """Da `timeout` segundos para vaciar la cola; lo que quede lo recupera
        el siguiente arranque."""
        for tarea in list(self._reintentos):
            tarea.cancel()
        if self._worker is None:
            return
        try:
            await asyncio.wait_for(self._cola.join(), timeout)
        except TimeoutError:
            logger.warning("Se detiene el despachador con %d avisos sin enviar.", self.pendientes)
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    async def recuperar_pendientes(self) -> int:
        desde = datetime.now(tz=timezone.utc) - VENTANA_RECUPERACION
        async with self._session_factory() as session:
            alertas = await SQLAlchemyAlertaRepository(session).listar_pendientes_de_notificar(
                desde, self._max_intentos
            )
            lecturas = SQLAlchemyLecturaRepository(session)
            avisos = []
            for alerta in alertas:
                lectura = await lecturas.obtener_por_id(alerta.lectura_inicial_id or alerta.reading_id)
                avisos.append(
                    AvisoExcursion(
                        alerta_id=alerta.id,
                        device_id=alerta.device_id,
                        temperatura=lectura.temperatura_interna if lectura else None,
                        captured_at=lectura.timestamp if lectura else alerta.created_at,
                        received_at=lectura.received_at if lectura else None,
                        time_quality=lectura.time_quality if lectura else None,
                    )
                )
        self.encolar_todos(avisos)
        if avisos:
            logger.info("Recuperados %d avisos de excursión sin enviar.", len(avisos))
        return len(avisos)

    async def _consumir(self) -> None:
        while True:
            aviso = await self._cola.get()
            try:
                await self.procesar(aviso)
            except Exception:
                logger.exception("Error no controlado procesando el aviso de %s", aviso.device_id)
            finally:
                self._cola.task_done()

    # ── Un intento ────────────────────────────────────────────────────────
    async def procesar(self, aviso: AvisoExcursion) -> ResultadoEnvio:
        """Un intento de envío del correo, con su auditoría. Público para que
        las pruebas lo ejerciten sin depender de la cola."""
        async with self._session_factory() as session:
            alertas = SQLAlchemyAlertaRepository(session)
            alerta = await alertas.obtener_por_id(aviso.alerta_id)
            if alerta is None:
                return ResultadoEnvio("alerta_inexistente")
            if alerta.notificada_en is not None:
                return ResultadoEnvio("ya_notificado", alerta.notificacion_intentos)
            if alerta.notificacion_intentos >= self._max_intentos:
                return ResultadoEnvio("fallido", alerta.notificacion_intentos)

            dispositivo = await SQLAlchemyDeviceRepository(session).obtener(aviso.device_id) or {}
            primer_intento = alerta.notificacion_intentos == 0
            if primer_intento:
                await self._avisar_por_canales_opcionales(aviso, dispositivo)

            if self._transporte is None:
                return ResultadoEnvio("deshabilitado")

            auditoria = AuditarAccionCriticaUseCase(
                SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
            )
            destinatario = dispositivo.get("responsable_email") or self._settings.correo_destino_respaldo
            if not destinatario:
                await self._auditar(auditoria, "NOTIFICACION_SIN_DESTINATARIO", aviso, {})
                await session.commit()
                return ResultadoEnvio("sin_destinatario")

            mensaje = componer_correo_excursion(
                aviso,
                remitente=self._settings.correo_remitente,
                destinatario=destinatario,
                frontend_url=self._settings.frontend_url,
                dispositivo=dispositivo,
            )
            try:
                await self._transporte.enviar(mensaje)
            except Exception as exc:
                intentos = await alertas.registrar_intento_fallido(aviso.alerta_id)
                await self._auditar(
                    auditoria,
                    "NOTIFICACION_FALLIDA",
                    aviso,
                    {
                        "destinatario": enmascarar_email(destinatario),
                        "intento": intentos,
                        "error": str(exc)[:200] if isinstance(exc, ErrorEnvioCorreo) else type(exc).__name__,
                    },
                )
                await session.commit()
                logger.warning("Aviso de %s no enviado (intento %d): %s", aviso.device_id, intentos, exc)
                if intentos < self._max_intentos:
                    self._programar_reintento(aviso, intentos)
                return ResultadoEnvio("fallido", intentos)

            ahora = datetime.now(tz=timezone.utc)
            await alertas.marcar_notificada(aviso.alerta_id, ahora)
            latencia = None
            if aviso.received_at is not None:
                recibido = aviso.received_at
                if recibido.tzinfo is None:
                    recibido = recibido.replace(tzinfo=timezone.utc)
                latencia = round((ahora - recibido).total_seconds(), 1)
            await self._auditar(
                auditoria,
                "NOTIFICACION_ENVIADA",
                aviso,
                {
                    "destinatario": enmascarar_email(destinatario),
                    "latencia_s": latencia,
                    "diferida": aviso.diferida,
                    "intento": alerta.notificacion_intentos + 1,
                },
            )
            await session.commit()
            logger.info("Aviso por correo enviado para %s (alerta %s)", aviso.device_id, aviso.alerta_id)
            return ResultadoEnvio("enviado", alerta.notificacion_intentos + 1)

    async def _auditar(
        self, auditoria: AuditarAccionCriticaUseCase, accion: str, aviso: AvisoExcursion, extra: dict
    ) -> None:
        await auditoria.execute(
            usuario_id=None,
            accion=accion,
            recurso=f"alertas/{aviso.alerta_id}",
            detalle={
                "alerta_id": str(aviso.alerta_id),
                "canal": "email",
                "transporte": getattr(self._transporte, "nombre", None),
                **extra,
            },
            ip_origen=None,
            device_id=aviso.device_id,
        )

    async def _avisar_por_canales_opcionales(self, aviso: AvisoExcursion, dispositivo: dict) -> None:
        try:
            await self._canales_opcionales.notificar_excursion_critica(
                device_id=aviso.device_id,
                temperatura=aviso.temperatura,
                timestamp=aviso.captured_at.isoformat(),
                telefono_destino=dispositivo.get("responsable_telefono"),
            )
        except Exception:  # pragma: no cover - los canales ya tragan sus fallos
            logger.exception("Fallo en canales opcionales para %s", aviso.device_id)

    def _programar_reintento(self, aviso: AvisoExcursion, intentos: int) -> None:
        espera = self._esperas[min(intentos - 1, len(self._esperas) - 1)] if self._esperas else 0

        async def reencolar() -> None:
            await asyncio.sleep(espera)
            self.encolar(aviso)

        tarea = asyncio.create_task(reencolar())
        self._reintentos.add(tarea)
        tarea.add_done_callback(self._reintentos.discard)
