"""HU-44: vigilancia térmica mientras la telemetría está retenida.

Durante una rotación por compromiso la ingesta no persiste ni acusa lo que
llega del nodo: puede venir de quien tenga el token filtrado. Eso no puede
significar quedarse ciego:

- al iniciar la retención se abre el incidente «monitoreo_suspendido» y se
  avisa (en lugar de un SIN_DATOS que confundiría la causa);
- cada lectura retenida y cada muestra `lectura_vigente` del nodo se evalúan
  con la misma regla 2-8 °C; si salen de rango se abre una «excursion_no_verificada»
  (crítica, origen no verificado) y se avisa. No se persiste como lectura ni
  entra en la cadena de lecturas: cuando el nodo pruebe el token nuevo, sus
  lecturas llegarán con su hora de captura y la regla abrirá la alerta normal;
- al probar la posesión se cierran ambos incidentes.

La excursión no verificada es UN episodio por ventana: no se cierra aunque la
temperatura vuelva a rango (no hay forma de saber si el dato es del nodo). Su
`detalle` guarda la lectura que lo abrió y, aparte, la última recibida
(`ultima_temperatura_interna`, `ultima_captura`, `ultima_en_rango`), para que
el tablero no presente el dato inicial como el actual.
"""

from datetime import datetime, timezone

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.vigilar_sin_datos import AvisoSinDatos, CambioIncidente, resolver_incidente
from src.domain.repositories.i_incidente_repository import (
    TIPO_EXCURSION_NO_VERIFICADA,
    TIPO_MONITOREO_SUSPENDIDO,
    IIncidenteRepository,
)
from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA


def _aviso(cambio: CambioIncidente) -> AvisoSinDatos:
    i = cambio.incidente
    return AvisoSinDatos(
        incidente_id=i.id, device_id=i.device_id, abierto_en=i.abierto_en, ultimo_reporte_en=i.ultimo_reporte_en,
        umbral_segundos=i.umbral_segundos, tipo=i.tipo, detalle=i.detalle,
    )


async def abrir_monitoreo_suspendido(
    incidentes: IIncidenteRepository,
    auditoria: AuditarAccionCriticaUseCase,
    device_id: str,
    ahora: datetime,
    motivo: str,
) -> tuple[CambioIncidente, AvisoSinDatos] | None:
    if await incidentes.abierto(device_id, TIPO_MONITOREO_SUSPENDIDO) is not None:
        return None
    incidente = await incidentes.abrir(
        device_id, ahora, None, 0, tipo=TIPO_MONITOREO_SUSPENDIDO, detalle={"motivo": motivo}
    )
    await auditoria.execute(
        usuario_id=None,
        accion="MONITOREO_SUSPENDIDO_DETECTADO",
        recurso=f"incidentes/{incidente.id}",
        detalle={"incidente_id": str(incidente.id), "causa": "rotacion_por_compromiso"},
        ip_origen=None,
        device_id=device_id,
    )
    cambio = CambioIncidente(incidente, "abierto")
    return cambio, _aviso(cambio)


async def evaluar_excursion_retenida(
    incidentes: IIncidenteRepository,
    auditoria: AuditarAccionCriticaUseCase,
    device_id: str,
    temperatura: float | None,
    capturada_en: datetime | None,
    origen: str,
    ahora: datetime,
    boot_id: int | None = None,
    seq_no: int | None = None,
) -> tuple[CambioIncidente, AvisoSinDatos] | None:
    """Misma regla que la ingesta normal (P3): una lectura válida fuera de
    2-8 °C es crítica. Un dato de sensor en falla (None) nunca la dispara."""
    if temperatura is None:
        return None
    abierto = await incidentes.abierto(device_id, TIPO_EXCURSION_NO_VERIFICADA)
    if abierto is not None:
        # Episodio ya abierto y avisado: solo se anota la última lectura.
        await incidentes.actualizar_detalle(abierto.id, {
            **(abierto.detalle or {}),
            "ultima_temperatura_interna": temperatura,
            "ultima_captura": capturada_en.isoformat() if capturada_en else None,
            "ultima_en_rango": RANGO_TERMICO_BPA.contiene(temperatura),
        })
        return None
    if RANGO_TERMICO_BPA.contiene(temperatura):
        return None
    detalle = {
        "temperatura_interna": temperatura,
        "captured_at": capturada_en.isoformat() if capturada_en else None,
        "origen": origen,  # lectura_retenida | lectura_vigente
        "boot_id": boot_id,
        "seq_no": seq_no,
        "origen_verificado": False,
    }
    incidente = await incidentes.abrir(
        device_id, ahora, capturada_en, 0, tipo=TIPO_EXCURSION_NO_VERIFICADA, detalle=detalle
    )
    retardo = (ahora - capturada_en).total_seconds() if capturada_en else None
    await auditoria.execute(
        usuario_id=None,
        accion="EXCURSION_NO_VERIFICADA_DETECTADA",
        recurso=f"incidentes/{incidente.id}",
        detalle={
            "incidente_id": str(incidente.id),
            **detalle,
            # Prueba C: tiempo desde la captura hasta la alerta.
            "captura_a_alerta_s": round(retardo, 1) if retardo is not None else None,
        },
        ip_origen=None,
        device_id=device_id,
    )
    cambio = CambioIncidente(incidente, "abierto")
    return cambio, _aviso(cambio)


async def cerrar_retencion(
    incidentes: IIncidenteRepository, auditoria: AuditarAccionCriticaUseCase, device_id: str, ahora: datetime
) -> list[CambioIncidente]:
    cambios = []
    for tipo in (TIPO_MONITOREO_SUSPENDIDO, TIPO_EXCURSION_NO_VERIFICADA):
        abierto = await incidentes.abierto(device_id, tipo)
        if abierto is not None:
            cambios.append(await resolver_incidente(incidentes, auditoria, abierto, ahora))
    return cambios


def ahora_utc() -> datetime:
    return datetime.now(tz=timezone.utc)
