"""Incidentes operativos que nacen de la ingesta de lecturas.

- `sensor_fault`: la segunda lectura seguida con el DS18B20 en falla lo abre
  (llega en ≤ 60 s con el intervalo de fábrica y una falla aislada no alerta);
  la siguiente lectura válida lo cierra.
- `colision_clave`: el nodo reutilizó una identidad (boot_id, seq_no) con otro
  contenido.

Los dos reutilizan la tabla `incidentes_operativos` (un incidente abierto por
dispositivo y tipo) y el aviso por correo de HU-54: una vez por incidente.
"""

from datetime import datetime

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.vigilar_sin_datos import AvisoSinDatos, CambioIncidente, resolver_incidente
from src.domain.repositories.i_incidente_repository import IIncidenteRepository

ACCION_ABIERTO = {"sensor_fault": "SENSOR_FAULT_DETECTADO", "colision_clave": "COLISION_CLAVE_DETECTADA"}


def aviso_de(cambio: CambioIncidente) -> AvisoSinDatos:
    i = cambio.incidente
    return AvisoSinDatos(
        incidente_id=i.id, device_id=i.device_id, abierto_en=i.abierto_en, ultimo_reporte_en=i.ultimo_reporte_en,
        umbral_segundos=i.umbral_segundos, tipo=i.tipo, detalle=i.detalle,
    )


async def abrir_incidente(
    incidentes: IIncidenteRepository,
    auditoria: AuditarAccionCriticaUseCase,
    device_id: str,
    tipo: str,
    ahora: datetime,
    detalle: dict,
    ultimo_reporte_en: datetime | None = None,
) -> tuple[CambioIncidente, AvisoSinDatos] | None:
    """Abre el incidente si no hay uno abierto del mismo tipo; devuelve el cambio
    y el aviso a encolar después del COMMIT, o None si ya estaba abierto."""
    if await incidentes.abierto(device_id, tipo) is not None:
        return None
    incidente = await incidentes.abrir(device_id, ahora, ultimo_reporte_en, 0, tipo=tipo, detalle=detalle)
    await auditoria.execute(
        usuario_id=None,
        accion=ACCION_ABIERTO[tipo],
        recurso=f"incidentes/{incidente.id}",
        detalle={"incidente_id": str(incidente.id), **detalle},
        ip_origen=None,
        device_id=device_id,
    )
    cambio = CambioIncidente(incidente, "abierto")
    return cambio, aviso_de(cambio)


async def cerrar_incidente(
    incidentes: IIncidenteRepository,
    auditoria: AuditarAccionCriticaUseCase,
    device_id: str,
    tipo: str,
    ahora: datetime,
) -> CambioIncidente | None:
    abierto = await incidentes.abierto(device_id, tipo)
    if abierto is None:
        return None
    return await resolver_incidente(incidentes, auditoria, abierto, ahora)
