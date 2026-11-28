from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import partial
from uuid import UUID

import anyio

from src.application.use_cases.exportar_reporte_bpa import (
    LIMITE_REGISTROS_REPORTE,
    listar_detectando_truncamiento,
)
from src.application.use_cases.verificar_integridad_registro import (
    VerificarIntegridadRegistroUseCase,
)
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.repositories.i_accion_correctiva_repository import IAccionCorrectivaRepository
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.repositories.i_checklist_repository import IChecklistRepository
from src.domain.repositories.i_device_repository import IDeviceRepository
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.repositories.i_reporte_repository import IReporteRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository
from src.infrastructure.pdf.generador_pdf import GeneradorReporteBPAPDF

# Rango objetivo de conservación de medicamentos termolábiles refrigerados
# (Manual de BPA, RM N.º 132-2015/MINSA).
TEMP_MIN_RANGO = 2.0
TEMP_MAX_RANGO = 8.0
VERIFICACIONES_POR_PAGINA = 200


def _como_utc(valor: datetime) -> datetime:
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


def _riesgo_mostrado(lectura: LecturaTermica) -> str | None:
    """HU-34/HU-38: el reporte cuenta y rotula el riesgo EFECTIVO (regla 2-8 °C
    + salvaguarda), no la clase cruda del modelo: el Random Forest es binario y
    nunca produce `excursion_critica`, así que contar por `nivel_riesgo` daba
    siempre cero excursiones. Lecturas antiguas sin riesgo efectivo calculado
    conservan su clase."""
    riesgo = lectura.riesgo_efectivo or lectura.nivel_riesgo
    return riesgo.value if riesgo is not None else None


def _calcular_estadisticas(lecturas: list[LecturaTermica]) -> dict:
    """Agregados del período. El % de tiempo en rango se aproxima por
    proporción de lecturas conformes: el muestreo del ESP32 es de cadencia
    fija, así que cada lectura representa el mismo intervalo. Esa equivalencia
    deja de valer si el dispositivo estuvo caído, por eso el reporte declara
    también cuántas lecturas quedaron sin clasificar."""
    total = len(lecturas)
    temperaturas = [
        lectura.temperatura_interna
        for lectura in lecturas
        if lectura.temperatura_interna is not None
    ]
    en_rango = sum(1 for t in temperaturas if TEMP_MIN_RANGO <= t <= TEMP_MAX_RANGO)

    por_nivel: dict[str, int] = {}
    for lectura in lecturas:
        clave = _riesgo_mostrado(lectura) or "sin_clasificar"
        por_nivel[clave] = por_nivel.get(clave, 0) + 1

    return {
        "total_lecturas": total,
        "lecturas_con_temperatura": len(temperaturas),
        "por_nivel": por_nivel,
        "alertas_criticas": por_nivel.get("excursion_critica", 0),
        # Sobre las lecturas que sí traen temperatura interna: incluir las
        # nulas en el denominador reportaría un cumplimiento peor que el real
        # por una caída de sensor, que es un problema distinto.
        "porcentaje_en_rango": (en_rango / len(temperaturas) * 100) if temperaturas else 0.0,
        "temp_minima": min(temperaturas) if temperaturas else None,
        "temp_maxima": max(temperaturas) if temperaturas else None,
        "temp_promedio": (sum(temperaturas) / len(temperaturas)) if temperaturas else None,
        # Calculadas sobre las lecturas cargadas (pueden estar recortadas).
        "fuente": "muestra",
    }


def _estadisticas_de_agregados(agregados: dict) -> dict:
    """HU-38: mismas cifras que `_calcular_estadisticas`, pero desde los
    agregados SQL del periodo COMPLETO: no dependen del tope del detalle."""
    con_temperatura = agregados["lecturas_con_temperatura"]
    por_nivel = agregados["por_nivel"]
    return {
        "total_lecturas": agregados["total_lecturas"],
        "lecturas_con_temperatura": con_temperatura,
        "por_nivel": por_nivel,
        "alertas_criticas": por_nivel.get("excursion_critica", 0),
        "porcentaje_en_rango": (agregados["lecturas_en_rango"] / con_temperatura * 100) if con_temperatura else 0.0,
        "temp_minima": agregados["temp_minima"],
        "temp_maxima": agregados["temp_maxima"],
        "temp_promedio": agregados["temp_promedio"],
        # Lecturas `unsynced` sin reanclar: sin hora de captura, fuera de las cifras.
        "lecturas_sin_hora": agregados.get("lecturas_sin_hora", 0),
        "fuente": "periodo_completo",
    }


def _valor_en(campo: str, actual: object, historial: list[dict], instante: datetime) -> object:
    """HU-51 esc. 4: valor de `campo` vigente en `instante`, reconstruido del
    historial de configuración: si hubo cambios posteriores, el valor anterior
    del primero de ellos; si no, el valor actual."""
    posteriores = [h for h in historial if h["campo"] == campo and _como_utc(h["created_at"]) > instante]
    if not posteriores:
        return actual
    return min(posteriores, key=lambda h: _como_utc(h["created_at"]))["valor_anterior"]


def _texto(valor: object) -> str | None:
    if valor is None:
        return None
    return valor.isoformat() if hasattr(valor, "isoformat") else str(valor)


def _solapa(payload: dict, desde: datetime, hasta: datetime) -> bool:
    try:
        inicio = _como_utc(datetime.fromisoformat(str(payload.get("desde"))))
        fin = _como_utc(datetime.fromisoformat(str(payload.get("hasta"))))
    except ValueError:
        return False
    return inicio <= hasta and fin >= desde


class ExportarReporteBPAPDFUseCase:
    """RF-13 / HU-38: reporte BPA descargable en PDF, con la verificación de
    integridad de la cadena SHA-256 incluida en el propio documento.

    Que el veredicto de integridad viaje DENTRO del PDF es lo que lo convierte
    en evidencia: un reporte que solo listara temperaturas sería indistinguible
    de una planilla editada a mano."""

    def __init__(
        self,
        lectura_repository: ILecturaRepository,
        alerta_repository: IAlertaRepository,
        trazabilidad_repository: ITrazabilidadRepository,
        reporte_repository: IReporteRepository,
        checklist_repository: IChecklistRepository,
        verificar_integridad: VerificarIntegridadRegistroUseCase,
        generador: GeneradorReporteBPAPDF | None = None,
        accion_repository: IAccionCorrectivaRepository | None = None,
        device_repository: IDeviceRepository | None = None,
        nombre_de_usuario: Callable[[UUID], Awaitable[str | None]] | None = None,
    ) -> None:
        self._lectura_repository = lectura_repository
        self._alerta_repository = alerta_repository
        self._trazabilidad_repository = trazabilidad_repository
        self._reporte_repository = reporte_repository
        self._checklist_repository = checklist_repository
        self._verificar_integridad = verificar_integridad
        self._generador = generador or GeneradorReporteBPAPDF()
        self._accion_repository = accion_repository
        self._device_repository = device_repository
        # HU-51: nombre visible de quien instaló (lo resuelve la interfaz).
        self._nombre_de_usuario = nombre_de_usuario

    async def _acciones_de(self, alertas: list) -> list[dict]:
        if self._accion_repository is None or not alertas:
            return []
        por_alerta = {a.id: a for a in alertas}
        acciones = await self._accion_repository.listar_por_alertas(list(por_alerta))
        rectificadas = {a.corrige_accion_id for a in acciones if a.corrige_accion_id}
        return [
            {
                "created_at": a.created_at,
                "device_id": por_alerta[a.alert_id].device_id,
                "decision": a.decision,
                "descripcion": a.descripcion,
                "justificacion": a.justificacion,
                "motivo_rectificacion": a.motivo_rectificacion,
                "vigente": a.id not in rectificadas,
            }
            for a in acciones
        ]

    async def _equipos_del_reporte(self, device_id: str | None, desde: datetime, hasta: datetime) -> list[dict]:
        """HU-51 esc. 4: ubicación e instalación VIGENTES DURANTE el periodo,
        reconstruidas del historial de configuración (no la ficha actual, que
        pudo cambiar después), los cambios dentro del periodo y la referencia
        al historial completo."""
        if self._device_repository is None:
            return []
        if device_id:
            uno = await self._device_repository.obtener(device_id)
            dispositivos = [uno] if uno else []
        else:
            dispositivos = await self._device_repository.listar()
        desde, hasta = _como_utc(desde), _como_utc(hasta)
        equipos = []
        for d in dispositivos:
            historial = await self._device_repository.listar_historial_configuracion(d["id"])
            cambios = [
                h for h in historial
                if h["campo"] == "ubicacion" and desde <= _como_utc(h["created_at"]) <= hasta
            ]
            instalacion = ("ubicacion", "fecha_instalacion", "observaciones_instalacion")
            posteriores = [
                h for h in historial if h["campo"] in instalacion and _como_utc(h["created_at"]) > hasta
            ]
            if posteriores:
                # Quien instaló lo vigente al cierre: autor del último cambio de
                # instalación hasta esa fecha (la ficha guarda solo el último).
                previos = [
                    h for h in historial if h["campo"] in instalacion and _como_utc(h["created_at"]) <= hasta
                ]
                instalador = max(previos, key=lambda h: _como_utc(h["created_at"]))["actor_id"] if previos else None
            else:
                instalador = d.get("instalado_por")
            nombre_instalador = (
                await self._nombre_de_usuario(instalador) if instalador and self._nombre_de_usuario else None
            )
            equipos.append(
                {
                    **d,
                    "ubicacion_periodo": _valor_en("ubicacion", d.get("ubicacion"), historial, hasta),
                    "ubicacion_inicio_periodo": _valor_en("ubicacion", d.get("ubicacion"), historial, desde),
                    "fecha_instalacion_periodo": _valor_en(
                        "fecha_instalacion", _texto(d.get("fecha_instalacion")), historial, hasta
                    ),
                    "observaciones_instalacion_periodo": _valor_en(
                        "observaciones_instalacion", d.get("observaciones_instalacion"), historial, hasta
                    ),
                    "instalado_por_periodo": str(instalador) if instalador else None,
                    "instalado_por_nombre": nombre_instalador,
                    "cambios_ubicacion": cambios,
                    "cambios_historial_total": len(historial),
                    "cambios_posteriores_al_periodo": len(posteriores),
                }
            )
        return equipos

    async def _estadisticas(
        self, device_id: str | None, desde: datetime, hasta: datetime, lecturas: list[LecturaTermica], alertas: list
    ) -> dict:
        """HU-38 esc. 3: resumen y excursiones de TODO el periodo con agregados
        SQL; el tope de 10 000 filas solo limita las tablas de detalle."""
        agregados = await self._lectura_repository.estadisticas_periodo(
            device_id, desde, hasta, TEMP_MIN_RANGO, TEMP_MAX_RANGO
        )
        estadisticas = (
            _estadisticas_de_agregados(agregados) if agregados is not None else _calcular_estadisticas(lecturas)
        )
        episodios = await self._alerta_repository.contar_por_nivel(device_id, desde, hasta)
        if episodios is None:
            episodios = {}
            for alerta in alertas:
                if alerta.nivel_riesgo is not None:
                    episodios[alerta.nivel_riesgo.value] = episodios.get(alerta.nivel_riesgo.value, 0) + 1
        estadisticas["episodios_por_nivel"] = episodios
        estadisticas["episodios_criticos"] = episodios.get("excursion_critica", 0)
        return estadisticas

    async def _ultima_verificacion(self, device_id: str | None, desde: datetime, hasta: datetime) -> dict | None:
        """HU-38 esc. 2: la verificación HU-37 más reciente cuyo periodo
        verificado se solapa con el del reporte (del dispositivo, si lo hay)."""
        desde, hasta = _como_utc(desde), _como_utc(hasta)
        offset = 0
        while True:
            eventos = await self._trazabilidad_repository.listar(
                tipo_evento="VERIFICACION_INTEGRIDAD", device_id=device_id,
                limite=VERIFICACIONES_POR_PAGINA, offset=offset,
            )
            for evento in eventos:  # más reciente primero
                if not _solapa(evento.payload, desde, hasta):
                    continue
                payload = evento.payload
                return {
                    "realizada_en": _como_utc(evento.timestamp).strftime("%Y-%m-%d %H:%M UTC"),
                    "device_id": payload.get("device_id"),
                    "desde": payload.get("desde"),
                    "hasta": payload.get("hasta"),
                    "resultado": payload.get("resultado") or ("integra" if payload.get("integra") else "corrupta"),
                    "total_bloques_verificados": payload.get("total_bloques_verificados"),
                    "hash_eslabon": evento.hash_actual,
                }
            if len(eventos) < VERIFICACIONES_POR_PAGINA:
                return None
            offset += len(eventos)

    async def execute(
        self,
        usuario_id: UUID,
        usuario_nombre: str,
        fecha_desde: datetime,
        fecha_hasta: datetime,
        device_id: str | None = None,
    ) -> bytes:
        # RF-13: las tres colecciones se ciñen al mismo periodo. Antes solo las
        # lecturas se acotaban, así que el reporte de un mes se acompañaba de
        # las alertas y la trazabilidad de todo el histórico.
        lecturas, lecturas_truncadas = await listar_detectando_truncamiento(
            self._lectura_repository.listar,
            device_id=device_id, desde=fecha_desde, hasta=fecha_hasta,
        )
        alertas, alertas_truncadas = await listar_detectando_truncamiento(
            self._alerta_repository.listar,
            device_id=device_id, desde=fecha_desde, hasta=fecha_hasta,
        )
        trazabilidad, trazabilidad_truncada = await listar_detectando_truncamiento(
            self._trazabilidad_repository.listar,
            device_id=device_id, desde=fecha_desde, hasta=fecha_hasta,
        )
        truncamiento = {
            "truncado": lecturas_truncadas or alertas_truncadas or trazabilidad_truncada,
            "lecturas_truncadas": lecturas_truncadas,
            "alertas_truncadas": alertas_truncadas,
            "trazabilidad_truncada": trazabilidad_truncada,
            "limite_por_coleccion": LIMITE_REGISTROS_REPORTE,
        }
        checklists = await self._checklist_repository.listar_por_rango_fechas(
            fecha_desde.date().isoformat(), fecha_hasta.date().isoformat()
        )

        # HU-38 esc. 3: si el listado de alertas se recortó, las excursiones
        # críticas del periodo se traen todas igualmente.
        if alertas_truncadas:
            vistas = {a.id for a in alertas}
            criticas = await self._alerta_repository.listar_por_nivel(
                "excursion_critica", device_id, fecha_desde, fecha_hasta
            )
            alertas = sorted(
                [*alertas, *(a for a in criticas if a.id not in vistas)],
                key=lambda a: _como_utc(a.created_at) if a.created_at else datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
        estadisticas = await self._estadisticas(device_id, fecha_desde, fecha_hasta, lecturas, alertas)

        # HU-38 esc. 1: acciones de las alertas del periodo y datos del equipo.
        acciones = await self._acciones_de(alertas)
        equipos = await self._equipos_del_reporte(device_id, fecha_desde, fecha_hasta)

        # HU-38 criterio 2: cuando el reporte es de un dispositivo concreto,
        # la verificación se acota a SU cadena (HU-25) — un digest específico
        # de esa unidad monitoreada, no del sistema completo.
        resultado = await self._verificar_integridad.execute(chain_id=device_id)
        veredicto = {
            "integra": resultado.integra,
            "total_registros": resultado.total_registros,
            "primer_registro_inconsistente": resultado.primer_registro_inconsistente,
            "registros_posteriores_afectados": resultado.registros_posteriores_afectados,
            "estado": resultado.estado.value,
            "total_rupturas": resultado.total_rupturas,
            "integra_desde": (
                resultado.integra_desde.strftime("%Y-%m-%d %H:%M UTC") if resultado.integra_desde else None
            ),
            "alcance": resultado.alcance,
            "verificado_en": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            # HU-38 esc. 2: referencia reproducible COMPLETA (64 hex), no un prefijo.
            "digest_cadena": resultado.hash_final or "N/A",
            "total_no_verificables": resultado.total_no_verificables,
            "versiones_hash": list(resultado.versiones_hash),
            "chain_id": device_id,
            "ultima_verificacion": await self._ultima_verificacion(device_id, fecha_desde, fecha_hasta),
        }

        # B-8: ReportLab es síncrono y tarda en reportes largos; se genera en un
        # hilo de trabajo para no detener la ingesta MQTT ni los streams SSE.
        pdf_bytes = await anyio.to_thread.run_sync(
            partial(
                self._generador.generar,
                lecturas=[
                    {
                        "timestamp": lectura.timestamp,
                        "temperatura_interna": lectura.temperatura_interna,
                        "temperatura_ambiental": lectura.temperatura_ambiental,
                        "humedad_ambiental": lectura.humedad_ambiental,
                        "apertura_refrigerador": lectura.apertura_refrigerador,
                        "nivel_riesgo": _riesgo_mostrado(lectura),
                        "confianza_ia": lectura.confianza_ia,
                        "origen": lectura.origen,
                    }
                    for lectura in lecturas
                ],
                alertas=[
                    {
                        "created_at": a.created_at,
                        "device_id": a.device_id,
                        "nivel_riesgo": a.nivel_riesgo.value if a.nivel_riesgo is not None else None,
                        "mensaje": a.mensaje,
                        "revisada": a.revisada,
                        "episodio_abierto": a.episodio_abierto,
                    }
                    for a in alertas
                ],
                trazabilidad=[
                    {
                        "timestamp": r.timestamp,
                        "tipo_evento": r.tipo_evento,
                        "previous_hash": r.hash_encadenado.previous_hash,
                        "hash_actual": r.hash_encadenado.hash_actual,
                    }
                    for r in trazabilidad
                ],
                checklists=[
                    {
                        "fecha": c.fecha,
                        "total_conformes": c.total_conformes(),
                        "conforme": c.es_conforme(),
                        "observaciones": c.observaciones,
                        # BE-020: la fecha declarada junto a cuándo se registró y
                        # modificó realmente; una declaración tardía queda a la vista.
                        "registrado_en": c.created_at,
                        "modificado_en": c.updated_at,
                    }
                    for c in checklists
                ],
                estadisticas=estadisticas,
                veredicto_integridad=veredicto,
                fecha_desde=fecha_desde.date().isoformat(),
                fecha_hasta=fecha_hasta.date().isoformat(),
                usuario=usuario_nombre,
                device_id=device_id,
                truncamiento=truncamiento,
                acciones=acciones,
                equipos=equipos,
            )
        )

        # Deja constancia de la exportación (quién descargó qué período).
        nombre_archivo = (
            f"reporte_bpa_{fecha_desde.date().isoformat()}_{fecha_hasta.date().isoformat()}.pdf"
        )
        await self._reporte_repository.registrar_exportacion(
            usuario_id=usuario_id,
            tipo_reporte="BPA_PDF",
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            archivo_url=nombre_archivo,
        )
        return pdf_bytes
