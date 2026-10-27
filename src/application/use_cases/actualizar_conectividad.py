"""HU-13: estado de conectividad del dispositivo.

Tres fuentes cambian el estado, con estas reglas (tabla 5.3 de la
especificación):

| Entrada            | Condición                                         | Estado   | Auditoría               |
|--------------------|---------------------------------------------------|----------|-------------------------|
| `online` (boot B)  | siempre                                           | online   | DISPOSITIVO_ONLINE      |
| `lwt_offline` (b)  | b nulo, o sin boot vigente, o b >= boot vigente    | offline  | DISPOSITIVO_OFFLINE     |
| `lwt_offline` (b)  | b < boot vigente                                  | sin cambio | LWT_OBSOLETO_IGNORADO |
| lectura válida     | el dispositivo figura offline                     | online   | DISPOSITIVO_ONLINE (telemetria) |

El LWT lo publica el broker cuando deja de oír al nodo (1,5 x keepalive). Si el
nodo reinició y ya volvió (boot B+1), un LWT atrasado de la sesión anterior
(boot B) no describe el presente. Y si el LWT llega tarde dentro del MISMO
arranque —reconexión rápida—, la siguiente lectura lo corrige: un nodo que
está enviando telemetría está, por definición, conectado.

El caso de uso no publica nada: devuelve el cambio para que el llamador emita
el evento SSE `conectividad` DESPUÉS del COMMIT (D2).

HU-44: si el backend expulsó la sesión hace poco (rotación, confirmación de la
consola, revocación), el LWT y el `online` que siguen se auditan con ese
`motivo` (p. ej. «reconexion_por_rotacion»): no son una caída del nodo.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.domain.repositories.i_device_repository import IDeviceRepository

OrigenConectividad = Literal["online", "lwt", "telemetria"]

# El LWT llega a 1,5 x keepalive y el nodo reconecta en segundos (0,5 s medido
# el 27/10/2026); pasado este margen el cambio ya no se atribuye a la expulsión.
VENTANA_EXPULSION = timedelta(seconds=120)
MOTIVO_EXPULSION = {
    "rotacion": "reconexion_por_rotacion",
    "consola_confirmada": "reconexion_por_confirmacion_consola",
    "reenvio_desafio": "reconexion_por_reenvio_desafio",
    "revocacion": "expulsion_por_revocacion",
}


@dataclass(frozen=True, slots=True)
class CambioConectividad:
    device_id: str
    estado: Literal["online", "offline"]
    desde: datetime
    boot_id: int | None
    origen: OrigenConectividad
    motivo: str | None = None

    def como_evento_sse(self) -> dict:
        return {
            "device_id": self.device_id,
            "estado": self.estado,
            "desde": self.desde.isoformat(),
            "boot_id": self.boot_id,
            "origen": self.origen,
            "motivo": self.motivo,
        }


class ActualizarConectividadUseCase:
    def __init__(
        self, device_repository: IDeviceRepository, auditoria: AuditarAccionCriticaUseCase
    ) -> None:
        self._devices = device_repository
        self._auditoria = auditoria

    async def execute(
        self,
        device_id: str,
        origen: OrigenConectividad,
        boot_id: int | None = None,
        momento: datetime | None = None,
        detalle: dict | None = None,
    ) -> CambioConectividad | None:
        momento = momento or datetime.now(tz=timezone.utc)
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=timezone.utc)
        dispositivo = await self._devices.obtener(device_id)
        if dispositivo is None:
            return None
        boot_vigente = dispositivo.get("conectividad_boot_id")
        if origen in ("lwt", "online"):
            expulsada = dispositivo.get("sesion_expulsada_en")
            if expulsada is not None and expulsada.tzinfo is None:
                expulsada = expulsada.replace(tzinfo=timezone.utc)
            motivo = MOTIVO_EXPULSION.get(dispositivo.get("sesion_expulsada_motivo") or "")
            if expulsada is not None and motivo and timedelta(0) <= momento - expulsada <= VENTANA_EXPULSION:
                detalle = {**(detalle or {}), "motivo": motivo}

        if origen == "telemetria":
            if dispositivo.get("estado_conectividad") == "online":
                return None
            return await self._aplicar(device_id, "online", boot_id, momento, origen, detalle)

        if origen == "online":
            return await self._aplicar(device_id, "online", boot_id, momento, origen, detalle)

        # origen == "lwt"
        if boot_id is not None and boot_vigente is not None and boot_id < boot_vigente:
            await self._auditoria.execute(
                usuario_id=None,
                accion="LWT_OBSOLETO_IGNORADO",
                recurso=f"dispositivos/{device_id}",
                detalle={"boot_id_lwt": boot_id, "boot_id_vigente": boot_vigente},
                ip_origen=None,
                device_id=device_id,
            )
            return None
        # El boot vigente no cambia con el LWT: identifica la sesión que se cayó.
        return await self._aplicar(device_id, "offline", boot_id, momento, origen, detalle)

    async def _aplicar(
        self,
        device_id: str,
        estado: Literal["online", "offline"],
        boot_id: int | None,
        momento: datetime,
        origen: OrigenConectividad,
        detalle: dict | None,
    ) -> CambioConectividad:
        await self._devices.actualizar_estado_conectividad(
            device_id, estado, boot_id=boot_id if estado == "online" else None, momento=momento
        )
        await self._auditoria.execute(
            usuario_id=None,
            accion="DISPOSITIVO_ONLINE" if estado == "online" else "DISPOSITIVO_OFFLINE",
            recurso=f"dispositivos/{device_id}",
            detalle={**(detalle or {}), "origen": origen, "boot_id": boot_id},
            ip_origen=None,
            device_id=device_id,
        )
        return CambioConectividad(device_id, estado, momento, boot_id, origen, (detalle or {}).get("motivo"))
