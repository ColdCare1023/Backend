"""RF-13 / HU-38: generación del reporte BPA en PDF.

Motor: ReportLab (Python puro). Se evaluó WeasyPrint —permite maquetar con
HTML/CSS— pero exige libpango y libcairo instaladas en el sistema operativo;
eso hacía que el backend no arrancara en máquinas sin esas librerías. ReportLab
se instala como wheel sin dependencias nativas, que es el requisito real aquí:
el reporte debe poder generarse en cualquier equipo donde corra el backend.
"""

from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Paleta alineada con la identidad visual del dashboard (verde pino sobre
# crema): el PDF debe leerse como el mismo producto, no como un anexo ajeno.
PINO = colors.HexColor("#1F4D3D")
PINO_CLARO = colors.HexColor("#E8F0EC")
CREMA = colors.HexColor("#FAF7F0")
BORDE = colors.HexColor("#D9D2C5")
TEXTO = colors.HexColor("#2A2724")
TENUE = colors.HexColor("#6B655C")

ROJO = colors.HexColor("#B3261E")
AMBAR = colors.HexColor("#9A6700")
VERDE = colors.HexColor("#1F6F43")

_COLOR_RIESGO = {
    "excursion_critica": ROJO,
    "riesgo_preventivo": AMBAR,
    "normal": VERDE,
}

_ETIQUETA_RIESGO = {
    "excursion_critica": "Excursión crítica",
    "riesgo_preventivo": "Riesgo preventivo",
    "normal": "Normal",
}

# Un PDF de inspección no necesita las 10 000 lecturas del período: necesita
# evidencia representativa y verificable. Se listan las más recientes y se
# declara explícitamente cuántas quedaron fuera (nunca se recorta en silencio).
MAX_FILAS_LECTURAS = 250
MAX_FILAS_ALERTAS = 100
MAX_FILAS_TRAZABILIDAD = 60


def _estilos() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle(
            "TituloReporte",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=19,
            leading=23,
            textColor=PINO,
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "subtitulo": ParagraphStyle(
            "SubtituloReporte",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            textColor=TENUE,
            spaceAfter=10,
        ),
        "seccion": ParagraphStyle(
            "Seccion",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=PINO,
            spaceBefore=14,
            spaceAfter=6,
        ),
        "cuerpo": ParagraphStyle(
            "Cuerpo",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12.5,
            textColor=TEXTO,
        ),
        "nota": ParagraphStyle(
            "Nota",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=7.8,
            leading=10.5,
            textColor=TENUE,
        ),
        "celda": ParagraphStyle(
            "Celda", parent=base["Normal"], fontName="Helvetica", fontSize=7.4, leading=9.5
        ),
        "hash": ParagraphStyle(
            "Hash", parent=base["Normal"], fontName="Courier", fontSize=6.6, leading=8.5
        ),
        "kpi_valor": ParagraphStyle(
            "KpiValor",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            alignment=TA_CENTER,
            textColor=PINO,
        ),
        "kpi_etiqueta": ParagraphStyle(
            "KpiEtiqueta",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=9,
            alignment=TA_CENTER,
            textColor=TENUE,
        ),
    }


def _hex(color: colors.Color) -> str:
    """`Color.hexval()` devuelve '0xrrggbb'; el markup de ReportLab espera
    '#rrggbb'."""
    return "#" + color.hexval()[2:]


def _fmt_dt(valor: Any) -> str:
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d %H:%M:%S")
    return str(valor or "—")


def _fmt_num(valor: Any, sufijo: str = "", decimales: int = 1) -> str:
    if valor is None:
        return "—"
    try:
        return f"{float(valor):.{decimales}f}{sufijo}"
    except (TypeError, ValueError):
        return str(valor)


def _fmt_puerta(valor: Any) -> str:
    # HU-04: None significa que el dispositivo no tiene MC-38 instalado, no
    # "puerta cerrada" — distinguirlo evita reportar un dato que no existe.
    if valor is None:
        return "N/D (sin MC-38)"
    return "Abierta" if valor else "Cerrada"


def _estilo_tabla_base(alineaciones: list[tuple]) -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), PINO),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 7.4),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("FONTSIZE", (0, 1), (-1, -1), 7.4),
            ("TOPPADDING", (0, 1), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 3.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CREMA]),
            ("GRID", (0, 0), (-1, -1), 0.35, BORDE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            *alineaciones,
        ]
    )


class GeneradorReporteBPAPDF:
    """Compone el PDF del reporte BPA a partir de datos ya consultados.

    No accede a base de datos ni conoce repositorios: recibe los datos del caso
    de uso y solo se ocupa de la maquetación."""

    def __init__(self) -> None:
        self._estilos = _estilos()

    # ── Encabezado y pie repetidos en cada página ─────────────────────────
    def _decorar_pagina(self, canvas, doc) -> None:
        canvas.saveState()
        ancho, alto = A4

        # Banda superior de identidad.
        canvas.setFillColor(PINO)
        canvas.rect(0, alto - 12 * mm, ancho, 12 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawString(18 * mm, alto - 8 * mm, "ThermoTrace — Reporte de Buenas Prácticas de Almacenamiento")

        # Pie con la declaración de verificabilidad y el folio de página.
        canvas.setFillColor(TENUE)
        canvas.setFont("Helvetica", 6.8)
        canvas.drawString(
            18 * mm,
            10 * mm,
            "Trazabilidad digital verificable mediante encadenamiento SHA-256 (RF-14/RF-15). "
            f"Generado {self._generado_en}.",
        )
        canvas.drawRightString(ancho - 18 * mm, 10 * mm, f"Página {canvas.getPageNumber()}")
        canvas.setStrokeColor(BORDE)
        canvas.setLineWidth(0.4)
        canvas.line(18 * mm, 13 * mm, ancho - 18 * mm, 13 * mm)
        canvas.restoreState()

    # ── Bloques del documento ─────────────────────────────────────────────
    def _portada(self, contexto: dict) -> list:
        e = self._estilos
        bloques = [
            Paragraph("Reporte BPA — Monitoreo de Cadena de Frío", e["titulo"]),
            Paragraph(
                f"Período: <b>{contexto['fecha_desde']}</b> a <b>{contexto['fecha_hasta']}</b>"
                f"{'  ·  Dispositivo: <b>' + contexto['device_id'] + '</b>' if contexto.get('device_id') else '  ·  Todos los dispositivos'}"
                f"<br/>Emitido por: {contexto['usuario']}",
                e["subtitulo"],
            ),
        ]
        return bloques

    def _tarjetas_kpi(self, est: dict) -> Table:
        e = self._estilos
        tarjetas = [
            (str(est["total_lecturas"]), "Lecturas registradas"),
            (f"{est['porcentaje_en_rango']:.1f}%", "Tiempo en rango 2–8 °C"),
            (str(est["alertas_criticas"]), "Excursiones críticas"),
            (
                _fmt_num(est["temp_promedio"], " °C"),
                f"Temp. media (mín {_fmt_num(est['temp_minima'])} / máx {_fmt_num(est['temp_maxima'])})",
            ),
        ]
        fila_valores = [Paragraph(valor, e["kpi_valor"]) for valor, _ in tarjetas]
        fila_etiquetas = [Paragraph(etiqueta, e["kpi_etiqueta"]) for _, etiqueta in tarjetas]

        tabla = Table([fila_valores, fila_etiquetas], colWidths=[43 * mm] * 4)
        tabla.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PINO_CLARO),
                    ("BOX", (0, 0), (-1, -1), 0.5, BORDE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, 0), 9),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return tabla

    def _seccion_resumen(self, est: dict) -> list:
        e = self._estilos
        distribucion = [
            [
                Paragraph("<b>Clasificación de riesgo</b>", e["celda"]),
                Paragraph("<b>Lecturas</b>", e["celda"]),
                Paragraph("<b>Proporción</b>", e["celda"]),
            ]
        ]
        total = max(est["total_lecturas"], 1)
        for clave in ("excursion_critica", "riesgo_preventivo", "normal"):
            cantidad = est["por_nivel"].get(clave, 0)
            distribucion.append(
                [
                    Paragraph(
                        f'<font color="{_hex(_COLOR_RIESGO[clave])}">■</font> {_ETIQUETA_RIESGO[clave]}',
                        e["celda"],
                    ),
                    str(cantidad),
                    f"{cantidad / total * 100:.1f}%",
                ]
            )
        sin_dato = est["por_nivel"].get("sin_clasificar", 0)
        if sin_dato:
            distribucion.append(
                [Paragraph("Sin clasificar (sensor sin dato)", e["celda"]), str(sin_dato), f"{sin_dato / total * 100:.1f}%"]
            )

        tabla = Table(distribucion, colWidths=[92 * mm, 40 * mm, 40 * mm])
        tabla.setStyle(
            _estilo_tabla_base(
                [
                    ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                    ("BACKGROUND", (0, 0), (-1, 0), PINO),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ]
            )
        )
        return [Paragraph("1. Resumen del período", e["seccion"]), self._tarjetas_kpi(est), Spacer(1, 8), tabla]

    def _seccion_integridad(self, veredicto: dict) -> list:
        e = self._estilos
        integra = veredicto.get("integra", False)
        color_fondo = PINO_CLARO if integra else colors.HexColor("#FBE9E7")
        color_borde = VERDE if integra else ROJO

        # HU-38 criterio 2: fecha de la verificación y una referencia/digest
        # reproducible — no hace falta imprimir el hash de cada lectura, pero
        # sí un valor verificable que identifique el estado de la cadena en
        # el momento de generar el reporte.
        fecha_verificacion = veredicto.get("verificado_en", "")
        digest = veredicto.get("digest_cadena", "")

        if integra:
            titulo = "✓ Cadena de trazabilidad íntegra"
            detalle = (
                f"Se verificaron <b>{veredicto.get('total_registros', 0)}</b> registros encadenados "
                "mediante SHA-256. Cada eslabón reproduce el hash esperado a partir del hash anterior, "
                "su marca temporal y su contenido: ningún registro del período fue alterado tras su emisión.<br/>"
                f"Verificado el: <b>{fecha_verificacion}</b><br/>"
                f"Referencia de la cadena (último hash íntegro): <font face='Courier'>{digest}</font>"
            )
        else:
            titulo = "✗ Cadena de trazabilidad comprometida"
            posicion = veredicto.get("primer_registro_inconsistente")
            detalle = (
                f"La verificación detectó una inconsistencia en la posición <b>{posicion}</b> de la cadena, "
                f"con <b>{veredicto.get('registros_posteriores_afectados', 0)}</b> registros posteriores afectados. "
                "El contenido de este reporte debe considerarse NO verificable hasta que un administrador "
                "aísle el registro corrupto y restaure la cadena (HU-47).<br/>"
                f"Verificado el: <b>{fecha_verificacion}</b>"
            )

        contenido = [
            [Paragraph(f"<b>{titulo}</b>", e["cuerpo"])],
            [Paragraph(detalle, e["cuerpo"])],
        ]
        tabla = Table(contenido, colWidths=[172 * mm])
        tabla.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), color_fondo),
                    ("BOX", (0, 0), (-1, -1), 0.6, color_borde),
                    ("LINEBEFORE", (0, 0), (0, -1), 2.5, color_borde),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, 0), 8),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
                ]
            )
        )
        return [Paragraph("2. Verificación de integridad", e["seccion"]), tabla]

    def _aviso_truncamiento(self, truncamiento: dict | None) -> list:
        """Declara que el reporte no contiene todo el periodo.

        A la cadencia de muestreo del nodo (30 s → 2.880 lecturas/día) el tope
        por colección cubre unos 3,5 días, de modo que un reporte mensual queda
        forzosamente recortado. Callarlo convertiría el documento en una
        evidencia engañosa ante una inspección: quien lo lee daría por completo
        un histórico que no lo es. El aviso va al principio, no en una nota al
        pie, porque condiciona la lectura de todo lo que sigue.
        """
        if not truncamiento or not truncamiento.get("truncado"):
            return []

        e = self._estilos
        limite = truncamiento.get("limite_por_coleccion", 0)
        recortadas = [
            nombre
            for clave, nombre in (
                ("lecturas_truncadas", "lecturas térmicas"),
                ("alertas_truncadas", "alertas"),
                ("trazabilidad_truncada", "registros de trazabilidad"),
            )
            if truncamiento.get(clave)
        ]

        detalle = (
            f"Este reporte está <b>INCOMPLETO</b>. El período solicitado contiene más "
            f"{' y más '.join(recortadas)} de los que admite un solo documento "
            f"(tope de <b>{limite:,}</b> registros por categoría). "
            "Para obtener el histórico completo, divida el período en rangos más cortos "
            "y conserve todos los reportes parciales como un único legajo."
        ).replace(",", ".")

        contenido = [
            [Paragraph("<b>⚠ Reporte parcial: faltan registros del período</b>", e["cuerpo"])],
            [Paragraph(detalle, e["cuerpo"])],
        ]
        tabla = Table(contenido, colWidths=[172 * mm])
        ambar = colors.HexColor("#B26A00")
        tabla.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF6E5")),
                    ("BOX", (0, 0), (-1, -1), 0.6, ambar),
                    ("LINEBEFORE", (0, 0), (0, -1), 2.5, ambar),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, 0), 8),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
                ]
            )
        )
        return [tabla]

    def _seccion_lecturas(self, lecturas: list[dict]) -> list:
        e = self._estilos
        bloques = [Paragraph("4. Registro histórico de lecturas térmicas", e["seccion"])]
        if not lecturas:
            bloques.append(Paragraph("No se registraron lecturas en el período seleccionado.", e["cuerpo"]))
            return bloques

        mostradas = lecturas[:MAX_FILAS_LECTURAS]
        filas = [["Fecha y hora", "Interna", "Ambiental", "Humedad", "Puerta", "Clasificación", "Confianza IA"]]
        for lectura in mostradas:
            nivel = lectura.get("nivel_riesgo")
            filas.append(
                [
                    _fmt_dt(lectura.get("timestamp")),
                    _fmt_num(lectura.get("temperatura_interna"), " °C"),
                    _fmt_num(lectura.get("temperatura_ambiental"), " °C"),
                    _fmt_num(lectura.get("humedad_ambiental"), " %"),
                    _fmt_puerta(lectura.get("apertura_refrigerador")),
                    _ETIQUETA_RIESGO.get(nivel, "Sin clasificar"),
                    _fmt_num(
                        None if lectura.get("confianza_ia") is None else float(lectura["confianza_ia"]) * 100,
                        " %",
                    ),
                ]
            )

        tabla = Table(
            filas,
            colWidths=[32 * mm, 20 * mm, 22 * mm, 20 * mm, 19 * mm, 32 * mm, 22 * mm],
            repeatRows=1,
        )
        estilo = _estilo_tabla_base([("ALIGN", (1, 1), (-1, -1), "CENTER")])
        # Colorear la celda de clasificación según el nivel de riesgo real.
        for indice, lectura in enumerate(mostradas, start=1):
            color = _COLOR_RIESGO.get(lectura.get("nivel_riesgo"))
            if color is not None:
                estilo.add("TEXTCOLOR", (5, indice), (5, indice), color)
                if lectura.get("nivel_riesgo") == "excursion_critica":
                    estilo.add("FONTNAME", (5, indice), (5, indice), "Helvetica-Bold")
        tabla.setStyle(estilo)
        bloques.append(tabla)

        if len(lecturas) > MAX_FILAS_LECTURAS:
            bloques.append(Spacer(1, 4))
            bloques.append(
                Paragraph(
                    f"Se muestran las {MAX_FILAS_LECTURAS} lecturas más recientes de un total de "
                    f"{len(lecturas)} en el período. Las estadísticas de la sección 1 se calcularon "
                    "sobre la totalidad de los registros, no sobre esta muestra.",
                    e["nota"],
                )
            )
        return bloques

    def _seccion_alertas(self, alertas: list[dict]) -> list:
        e = self._estilos
        bloques = [Paragraph("5. Alertas generadas y su atención", e["seccion"])]
        if not alertas:
            bloques.append(
                Paragraph("No se generaron alertas térmicas en el período seleccionado.", e["cuerpo"])
            )
            return bloques

        filas = [["Fecha de apertura", "Dispositivo", "Nivel", "Mensaje", "Estado", "Revisada"]]
        for alerta in alertas[:MAX_FILAS_ALERTAS]:
            filas.append(
                [
                    _fmt_dt(alerta.get("created_at")),
                    str(alerta.get("device_id") or "—"),
                    _ETIQUETA_RIESGO.get(alerta.get("nivel_riesgo"), str(alerta.get("nivel_riesgo"))),
                    Paragraph(str(alerta.get("mensaje") or "—"), e["celda"]),
                    "Abierta" if alerta.get("episodio_abierto") else "Cerrada",
                    "Sí" if alerta.get("revisada") else "No",
                ]
            )
        tabla = Table(
            filas, colWidths=[30 * mm, 28 * mm, 26 * mm, 52 * mm, 18 * mm, 18 * mm], repeatRows=1
        )
        tabla.setStyle(_estilo_tabla_base([("ALIGN", (1, 1), (2, -1), "CENTER"), ("ALIGN", (4, 1), (-1, -1), "CENTER")]))
        bloques.append(tabla)
        if len(alertas) > MAX_FILAS_ALERTAS:
            bloques.append(Spacer(1, 4))
            bloques.append(
                Paragraph(
                    f"Se muestran las {MAX_FILAS_ALERTAS} alertas más recientes de {len(alertas)} en el período.",
                    e["nota"],
                )
            )
        return bloques

    def _seccion_trazabilidad(self, registros: list[dict]) -> list:
        e = self._estilos
        bloques = [
            Paragraph("6. Cadena de trazabilidad (evidencia SHA-256)", e["seccion"]),
            Paragraph(
                "Cada fila es un eslabón de la cadena. El campo <i>hash anterior</i> de un registro "
                "reproduce el <i>hash actual</i> del registro previo: alterar cualquier evento pasado "
                "rompe todos los hashes posteriores y la verificación lo detecta.",
                e["cuerpo"],
            ),
            Spacer(1, 5),
        ]
        if not registros:
            bloques.append(Paragraph("Sin registros de trazabilidad en el período.", e["cuerpo"]))
            return bloques

        filas = [["Fecha y hora", "Evento", "Hash anterior", "Hash actual"]]
        for registro in registros[:MAX_FILAS_TRAZABILIDAD]:
            filas.append(
                [
                    _fmt_dt(registro.get("timestamp")),
                    str(registro.get("tipo_evento") or "—"),
                    Paragraph(str(registro.get("previous_hash", ""))[:32] + "…", e["hash"]),
                    Paragraph(str(registro.get("hash_actual", ""))[:32] + "…", e["hash"]),
                ]
            )
        tabla = Table(filas, colWidths=[30 * mm, 42 * mm, 50 * mm, 50 * mm], repeatRows=1)
        tabla.setStyle(_estilo_tabla_base([]))
        bloques.append(tabla)
        bloques.append(Spacer(1, 4))
        bloques.append(
            Paragraph(
                "Los hashes se muestran truncados a 32 de sus 64 caracteres hexadecimales por legibilidad. "
                "El valor completo de cada eslabón está disponible en el módulo de Trazabilidad del sistema "
                "y es el que emplea la verificación automática."
                + (
                    f" Se listan los {MAX_FILAS_TRAZABILIDAD} eslabones más recientes de {len(registros)}."
                    if len(registros) > MAX_FILAS_TRAZABILIDAD
                    else ""
                ),
                e["nota"],
            )
        )
        return bloques

    def _seccion_checklist(self, checklists: list[dict]) -> list:
        e = self._estilos
        bloques = [Paragraph("3. Verificaciones BPA declaradas (HU-37)", e["seccion"])]
        if not checklists:
            bloques.append(
                Paragraph(
                    "No se registraron checklists de Buenas Prácticas de Almacenamiento en el período.",
                    e["cuerpo"],
                )
            )
            return bloques

        filas = [["Fecha", "Ítems conformes", "Resultado", "Observaciones"]]
        for checklist in checklists:
            conforme = checklist.get("conforme")
            filas.append(
                [
                    str(checklist.get("fecha") or "—"),
                    f"{checklist.get('total_conformes', 0)} / 10",
                    "Conforme" if conforme else "Con observaciones",
                    Paragraph(str(checklist.get("observaciones") or "—"), e["celda"]),
                ]
            )
        tabla = Table(filas, colWidths=[26 * mm, 30 * mm, 36 * mm, 80 * mm], repeatRows=1)
        estilo = _estilo_tabla_base([("ALIGN", (1, 1), (2, -1), "CENTER")])
        for indice, checklist in enumerate(checklists, start=1):
            color = VERDE if checklist.get("conforme") else AMBAR
            estilo.add("TEXTCOLOR", (2, indice), (2, indice), color)
        tabla.setStyle(estilo)
        bloques.append(tabla)
        return bloques

    def _cierre(self) -> list:
        e = self._estilos
        return [
            Spacer(1, 14),
            Paragraph(
                "Este documento fue generado automáticamente por el sistema ThermoTrace a partir de los "
                "registros almacenados. Su contenido es verificable: la sección 2 declara el resultado de "
                "recomputar la cadena de hashes SHA-256 sobre la totalidad de los eventos registrados. "
                "Elaborado en el marco de la tesis de monitoreo IoT de cadena de frío farmacéutica, como "
                "referencia frente al Manual de Buenas Prácticas de Almacenamiento (RM N.º 132-2015/MINSA).<br/><br/>"
                "<b>Este documento constituye evidencia del monitoreo y la trazabilidad térmica del período "
                "indicado; no declara por sí mismo cumplimiento sanitario integral.</b> La certificación "
                "sanitaria corresponde exclusivamente a la autoridad competente (DIGEMID).",
                e["nota"],
            ),
        ]

    # ── Punto de entrada ──────────────────────────────────────────────────
    def generar(
        self,
        *,
        lecturas: list[dict],
        alertas: list[dict],
        trazabilidad: list[dict],
        checklists: list[dict],
        estadisticas: dict,
        veredicto_integridad: dict,
        fecha_desde: str,
        fecha_hasta: str,
        usuario: str,
        device_id: str | None = None,
        truncamiento: dict | None = None,
    ) -> bytes:
        self._generado_en = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=20 * mm,
            bottomMargin=18 * mm,
            title=f"Reporte BPA {fecha_desde} a {fecha_hasta}",
            author="ThermoTrace",
            subject="Reporte de Buenas Prácticas de Almacenamiento — cadena de frío",
        )

        contexto = {
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
            "usuario": usuario,
            "device_id": device_id,
        }

        historia: list = []
        historia += self._portada(contexto)
        # Antes del resumen: si el documento está recortado, hay que saberlo
        # antes de leer ninguna cifra.
        historia += self._aviso_truncamiento(truncamiento)
        historia += self._seccion_resumen(estadisticas)
        historia += [KeepTogether(self._seccion_integridad(veredicto_integridad))]
        historia += self._seccion_checklist(checklists)
        historia.append(PageBreak())
        historia += self._seccion_lecturas(lecturas)
        historia.append(PageBreak())
        historia += self._seccion_alertas(alertas)
        historia += self._seccion_trazabilidad(trazabilidad)
        historia += self._cierre()

        doc.build(historia, onFirstPage=self._decorar_pagina, onLaterPages=self._decorar_pagina)
        return buffer.getvalue()
