"""HU-54: incidente SIN_DATOS persistente.

Un nodo que deja de reportar no produce lecturas, así que ninguna regla de la
ingesta puede detectarlo: hace falta alguien que mire el reloj. La tarea
periódica del backend (`interface/tareas.py`) ejecuta este caso de uso cada
pocos segundos:

- abre un incidente cuando el silencio del nodo alcanza N x su intervalo de
  captura (3 x 30 s = 90 s de fábrica), con auditoría en la cadena del
  dispositivo y un aviso que se entrega DESPUÉS del COMMIT;
- lo resuelve cuando vuelven las lecturas (también lo hace la propia ingesta,
  ver `resolver_sin_datos_por_lectura`).

El incidente es operativo, no térmico: no pasa por `thermal_alerts` ni por la
IA. Un nodo que nunca reportó (recién dado de alta) no abre incidente: el
tablero lo muestra SIN_DATOS, pero no hay nada que avisar todavía.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.domain.repositories.i_device_repository import IDeviceRepository
from src.domain.repositories.i_incidente_repository import IIncidenteRepository, IncidenteOperativo
from src.domain.repositories.i_lectura_repository import ILecturaRepository

# Ciclos perdidos tolerados antes de declarar SIN_DATOS: una reconexión breve
# no dispara la alarma (HU-33/HU-54).
N_INTERVALOS_SIN_DATOS = 3
INTERVALO_REPORTE_POR_DEFECTO = 30


def umbral_sin_datos(intervalo_segundos: int | None, n_intervalos: int = N_INTERVALOS_SIN_DATOS) -> int:
    return n_intervalos * (intervalo_segundos or INTERVALO_REPORTE_POR_DEFECTO)


def _como_utc(instante: datetime | None) -> datetime | None:
    if instante is None:
        return None
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class AvisoSinDatos:
    """Aviso pendiente de un incidente SIN_DATOS recién abierto. Se entrega al
    despachador después del COMMIT, igual que `AvisoExcursion`."""

    incidente_id: UUID
    device_id: str
    abierto_en: datetime
    ultimo_reporte_en: datetime | None
    umbral_segundos: int


@dataclass(frozen=True, slots=True)
class CambioIncidente:
    incidente: IncidenteOperativo
    tipo: str  # "abierto" | "resuelto"

    def como_evento_sse(self) -> dict:
        return {
            "id": str(self.incidente.id),
            "device_id": self.incidente.device_id,
            "tipo": self.incidente.tipo,
            "estado": "activo" if self.tipo == "abierto" else "resuelto",
            "detectado_en": self.incidente.abierto_en.isoformat(),
            "ultimo_reporte_en": (
                self.incidente.ultimo_reporte_en.isoformat() if self.incidente.ultimo_reporte_en else None
            ),
            "umbral_silencio_segundos": self.incidente.umbral_segundos,
            "recuperado_en": self.incidente.resuelto_en.isoformat() if self.incidente.resuelto_en else None,
        }


async def _resolver(
    incidentes: IIncidenteRepository,
    auditoria: AuditarAccionCriticaUseCase,
    abierto: IncidenteOperativo,
    cuando: datetime,
) -> CambioIncidente:
    resuelto = await incidentes.resolver(abierto.id, cuando)
    duracion = (cuando - _como_utc(abierto.abierto_en)).total_seconds()
    await auditoria.execute(
        usuario_id=None,
        accion="SIN_DATOS_RESUELTO",
        recurso=f"incidentes/{abierto.id}",
        detalle={
            "incidente_id": str(abierto.id),
            "abierto_en": _como_utc(abierto.abierto_en).isoformat(),
            "resuelto_en": cuando.isoformat(),
            "duracion_s": round(max(duracion, 0.0), 1),
        },
        ip_origen=None,
        device_id=abierto.device_id,
    )
    return CambioIncidente(resuelto, "resuelto")


async def resolver_sin_datos_por_lectura(
    incidentes: IIncidenteRepository,
    auditoria: AuditarAccionCriticaUseCase,
    device_id: str,
    recibida_en: datetime,
) -> CambioIncidente | None:
    """Una lectura del nodo cierra el incidente abierto en la misma transacción
    que la persiste: no espera a la siguiente pasada de la tarea periódica."""
    abierto = await incidentes.abierto(device_id)
    if abierto is None:
        return None
    return await _resolver(incidentes, auditoria, abierto, _como_utc(recibida_en))


class VigilarSinDatosUseCase:
    def __init__(
        self,
        device_repository: IDeviceRepository,
        lectura_repository: ILecturaRepository,
        incidente_repository: IIncidenteRepository,
        auditoria: AuditarAccionCriticaUseCase,
        n_intervalos: int = N_INTERVALOS_SIN_DATOS,
    ) -> None:
        self._dispositivos = device_repository
        self._lecturas = lectura_repository
        self._incidentes = incidente_repository
        self._auditoria = auditoria
        self._n = n_intervalos
        self.avisos_pendientes: list[AvisoSinDatos] = []

    async def execute(self, ahora: datetime | None = None) -> list[CambioIncidente]:
        ahora = _como_utc(ahora) or datetime.now(tz=timezone.utc)
        cambios: list[CambioIncidente] = []
        for dispositivo in await self._dispositivos.listar():
            abierto = await self._incidentes.abierto(dispositivo["id"])
            if not dispositivo.get("activo", True):
                # Un nodo dado de baja deja de reportar a propósito.
                if abierto is not None:
                    cambios.append(await _resolver(self._incidentes, self._auditoria, abierto, ahora))
                continue
            ultima = await self._lecturas.ultima_recibida_por_device(dispositivo["id"])
            if ultima is None:
                continue
            ultimo_reporte = _como_utc(ultima.received_at or ultima.timestamp)
            umbral = umbral_sin_datos(dispositivo.get("intervalo_reporte_segundos"), self._n)
            silencio = (ahora - ultimo_reporte).total_seconds()
            if silencio >= umbral and abierto is None:
                cambios.append(await self._abrir(dispositivo, ultimo_reporte, umbral))
            elif silencio < umbral and abierto is not None:
                cambios.append(await _resolver(self._incidentes, self._auditoria, abierto, ultimo_reporte))
        return cambios

    async def _abrir(self, dispositivo: dict, ultimo_reporte: datetime, umbral: int) -> CambioIncidente:
        detectado = ultimo_reporte + timedelta(seconds=umbral)
        incidente = await self._incidentes.abrir(dispositivo["id"], detectado, ultimo_reporte, umbral)
        latido = _como_utc(dispositivo.get("ultimo_latido_en"))
        await self._auditoria.execute(
            usuario_id=None,
            accion="SIN_DATOS_DETECTADO",
            recurso=f"incidentes/{incidente.id}",
            detalle={
                "incidente_id": str(incidente.id),
                "ultimo_reporte_en": ultimo_reporte.isoformat(),
                "umbral_s": umbral,
                "intervalo_s": dispositivo.get("intervalo_reporte_segundos"),
                # Un latido posterior a la última lectura: el nodo vive pero
                # no entrega lecturas (sensor, cola o ACK), no está apagado.
                "nodo_con_latido": bool(latido and latido > ultimo_reporte),
            },
            ip_origen=None,
            device_id=dispositivo["id"],
        )
        self.avisos_pendientes.append(
            AvisoSinDatos(
                incidente_id=incidente.id, device_id=dispositivo["id"], abierto_en=detectado,
                ultimo_reporte_en=ultimo_reporte, umbral_segundos=umbral,
            )
        )
        return CambioIncidente(incidente, "abierto")
