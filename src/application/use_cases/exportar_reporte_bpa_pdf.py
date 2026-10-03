from datetime import datetime, timezone
from uuid import UUID

from src.application.use_cases.exportar_reporte_bpa import (
    LIMITE_REGISTROS_REPORTE,
    listar_detectando_truncamiento,
)
from src.application.use_cases.verificar_integridad_registro import (
    VerificarIntegridadRegistroUseCase,
)
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.repositories.i_checklist_repository import IChecklistRepository
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.repositories.i_reporte_repository import IReporteRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository
from src.infrastructure.pdf.generador_pdf import GeneradorReporteBPAPDF

# Rango objetivo de conservación de medicamentos termolábiles refrigerados
# (Manual de BPA, RM N.º 132-2015/MINSA).
TEMP_MIN_RANGO = 2.0
TEMP_MAX_RANGO = 8.0


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
        clave = lectura.nivel_riesgo.value if lectura.nivel_riesgo is not None else "sin_clasificar"
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
    }


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
    ) -> None:
        self._lectura_repository = lectura_repository
        self._alerta_repository = alerta_repository
        self._trazabilidad_repository = trazabilidad_repository
        self._reporte_repository = reporte_repository
        self._checklist_repository = checklist_repository
        self._verificar_integridad = verificar_integridad
        self._generador = generador or GeneradorReporteBPAPDF()

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

        # HU-38 criterio 2: cuando el reporte es de un dispositivo concreto,
        # la verificación se acota a SU cadena (HU-25) — un digest específico
        # de esa unidad monitoreada, no del sistema completo.
        resultado = await self._verificar_integridad.execute(chain_id=device_id)
        veredicto = {
            "integra": resultado.integra,
            "total_registros": resultado.total_registros,
            "primer_registro_inconsistente": resultado.primer_registro_inconsistente,
            "registros_posteriores_afectados": resultado.registros_posteriores_afectados,
            "verificado_en": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "digest_cadena": (resultado.hash_final or "")[:16] + "…" if resultado.hash_final else "N/A",
        }

        pdf_bytes = self._generador.generar(
            lecturas=[
                {
                    "timestamp": lectura.timestamp,
                    "temperatura_interna": lectura.temperatura_interna,
                    "temperatura_ambiental": lectura.temperatura_ambiental,
                    "humedad_ambiental": lectura.humedad_ambiental,
                    "apertura_refrigerador": lectura.apertura_refrigerador,
                    "nivel_riesgo": lectura.nivel_riesgo.value if lectura.nivel_riesgo is not None else None,
                    "confianza_ia": lectura.confianza_ia,
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
                }
                for c in checklists
            ],
            estadisticas=_calcular_estadisticas(lecturas),
            veredicto_integridad=veredicto,
            fecha_desde=fecha_desde.date().isoformat(),
            fecha_hasta=fecha_hasta.date().isoformat(),
            usuario=usuario_nombre,
            device_id=device_id,
            truncamiento=truncamiento,
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
