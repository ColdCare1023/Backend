"""RF-13 / HU-38: generación del reporte BPA en PDF.

Motor: ReportLab (Python puro). Se evaluó WeasyPrint —permite maquetar con
HTML/CSS— pero exige libpango y libcairo instaladas en el sistema operativo;
eso hacía que el backend no arrancara en máquinas sin esas librerías. ReportLab
se instala como wheel sin dependencias nativas, que es el requisito real aquí:
el reporte debe poder generarse en cualquier equipo donde corra el backend.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

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


def _texto(valor: Any, vacio: str = "—") -> str:
    """BE-004: texto plano seguro para `Paragraph`.

    `Paragraph` interpreta un mini-lenguaje XML (`<a href>`, `<img src>`,
    `<font>`): un dato del usuario sin escapar podía insertar enlaces o
    imágenes remotas/locales en un documento presentado como evidencia, o
    romper la generación del PDF. Todo valor dinámico pasa por aquí."""
    if valor is None or valor == "":
        return vacio
    return escape(str(valor))


def _hex(color: colors.Color) -> str:
    """`Color.hexval()` devuelve '0xrrggbb'; el markup de ReportLab espera
    '#rrggbb'."""
    return "#" + color.hexval()[2:]


def _fmt_dt(valor: Any) -> str:
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d %H:%M:%S")
    return str(valor or "—")


def _utc(valor: datetime) -> datetime:
    """SQLite devuelve datetimes naive aunque la columna sea timezone-aware."""
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


def _registro_checklist(checklist: dict) -> str:
    """BE-020: markup (ya escapado) con cuándo se registró realmente el
    checklist y, si se corrigió después, cuándo. Una declaración tardía o
    modificada queda visible junto a la fecha declarada."""
    registrado = checklist.get("registrado_en")
    modificado = checklist.get("modificado_en")
    if not isinstance(registrado, datetime):
        return _texto(None)
    lineas = [_texto(registrado.strftime("%Y-%m-%d %H:%M"))]
    if isinstance(modificado, datetime) and _utc(modificado) - _utc(registrado) > timedelta(minutes=1):
        lineas.append(_texto(f"modif. {modificado.strftime('%Y-%m-%d %H:%M')}"))
    return "<br/>".join(lineas)


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
        canvas.drawString(18 * mm, alto - 8 * mm, "ColdCare — Reporte de Buenas Prácticas de Almacenamiento")

        # Pie con la declaración de verificabilidad y el folio de página.
        canvas.setFillColor(TENUE)
        canvas.setFont("Helvetica", 6.8)
        canvas.drawString(
            18 * mm,
            10 * mm,
            "Trazabilidad digital verificable mediante encadenamiento SHA-256 / HMAC-SHA256 (RF-14/RF-15). "
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
                f"Período: <b>{_texto(contexto['fecha_desde'])}</b> a <b>{_texto(contexto['fecha_hasta'])}</b>"
                f"{'  ·  Dispositivo: <b>' + _texto(contexto['device_id']) + '</b>' if contexto.get('device_id') else '  ·  Todos los dispositivos'}"
                f"<br/>Emitido por: {_texto(contexto['usuario'])}",
                e["subtitulo"],
            ),
        ]
        return bloques

    def _tarjetas_kpi(self, est: dict) -> Table:
        e = self._estilos
        episodios = est.get("episodios_criticos")
        tarjetas = [
            (str(est["total_lecturas"]), "Lecturas registradas"),
            (f"{est['porcentaje_en_rango']:.1f}%", "Tiempo en rango 2–8 °C"),
            (
                (str(episodios), f"Excursiones críticas ({est['alertas_criticas']} lecturas)")
                if episodios is not None
                else (str(est["alertas_criticas"]), "Excursiones críticas")
            ),
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
        bloques = [Paragraph("1. Resumen del período", e["seccion"]), self._tarjetas_kpi(est), Spacer(1, 8), tabla]
        if est.get("fuente") == "periodo_completo":
            # HU-38 esc. 3: las cifras no dependen del tope de las tablas de detalle.
            bloques += [
                Spacer(1, 4),
                Paragraph(
                    f"Cifras calculadas sobre la totalidad del período ({est['total_lecturas']} lecturas y "
                    f"{est.get('episodios_criticos', 0)} episodio(s) de excursión crítica), con independencia del "
                    "tope de filas de las tablas de detalle.",
                    e["nota"],
                ),
            ]
        sin_hora = est.get("lecturas_sin_hora") or 0
        if sin_hora:
            # HU-38/HU-31: el periodo se mide con la hora de captura, igual que la curva.
            bloques += [
                Spacer(1, 4),
                Paragraph(
                    f"{sin_hora} lectura(s) del período llegaron sin hora de captura (el nodo aún no había "
                    "sincronizado su reloj) y no se incluyen en estas cifras.",
                    e["nota"],
                ),
            ]
        return bloques

    def _seccion_integridad(self, veredicto: dict) -> list:
        e = self._estilos
        estado = veredicto.get("estado") or ("integra" if veredicto.get("integra") else "ruptura_detectada")
        integra = estado == "integra"
        no_concluida = estado == "no_concluida"
        color_borde = VERDE if integra else (AMBAR if no_concluida or estado == "ruptura_aislada" else ROJO)
        color_fondo = PINO_CLARO if integra else (
            colors.HexColor("#FFF6E5") if color_borde == AMBAR else colors.HexColor("#FBE9E7")
        )

        # HU-38 criterio 2: fecha de la verificación y una referencia/digest
        # reproducible COMPLETA (los 64 caracteres hexadecimales) — no hace
        # falta imprimir el hash de cada lectura, pero sí el valor que permite
        # reproducir el estado de la cadena al generar el reporte.
        fecha_verificacion = _texto(veredicto.get("verificado_en"))
        digest = _texto(veredicto.get("digest_cadena"), "N/A")
        cadena = (
            f"la cadena del dispositivo <b>{_texto(veredicto['chain_id'])}</b>"
            if veredicto.get("chain_id")
            else "todas las cadenas"
        )
        referencia = (
            f"Verificado el: <b>{fecha_verificacion}</b> ({cadena}).<br/>"
            f"Referencia reproducible (hash del último eslabón examinado, 64 hex):<br/>"
            f"<font face='Courier' size='7'>{digest}</font>"
        )

        if integra:
            titulo = "✓ No se detectaron alteraciones en la cadena de trazabilidad"
            detalle = (
                f"Se verificaron <b>{veredicto.get('total_registros', 0)}</b> registros encadenados. Cada eslabón "
                "reproduce el hash esperado (SHA-256 o HMAC-SHA256 según su versión) a partir del hash anterior, "
                "su marca temporal y su contenido, y enlaza con el hash almacenado del eslabón previo.<br/>"
                + referencia
            )
        elif no_concluida:
            titulo = "◌ Verificación no concluida"
            detalle = (
                f"<b>{veredicto.get('total_no_verificables', 0)}</b> eslabón(es) HMAC-SHA256 (v3) no se pudieron "
                "verificar porque la clave de trazabilidad no estaba disponible. El resultado no se informa como "
                "íntegro ni como alterado.<br/>" + referencia
            )
        elif estado == "ruptura_aislada":
            titulo = "◐ Cadena con ruptura aislada"
            detalle = (
                f"La cadena registra <b>{veredicto.get('total_rupturas', 0)}</b> alteración(es) histórica(s), "
                "todas aisladas por un administrador. Los registros alterados no son verificables; "
                f"la cadena vuelve a ser verificable desde <b>{_texto(veredicto.get('integra_desde'))}</b>.<br/>"
                + referencia
            )
        else:
            titulo = "✗ Cadena de trazabilidad comprometida"
            posicion = veredicto.get("primer_registro_inconsistente")
            detalle = (
                f"La verificación detectó una inconsistencia en la posición <b>{_texto(posicion)}</b> de la cadena, "
                f"con <b>{veredicto.get('registros_posteriores_afectados', 0)}</b> registros posteriores afectados. "
                "El contenido de este reporte debe considerarse NO verificable hasta que un administrador "
                "aísle el registro corrupto y restaure la cadena.<br/>" + referencia
            )

        contenido = [
            [Paragraph(f"<b>{titulo}</b>", e["cuerpo"])],
            [Paragraph(detalle, e["cuerpo"])],
        ]
        # HU-38 esc. 2: última verificación por dispositivo y periodo (HU-37)
        # que cubre este reporte, con su resultado, fecha y eslabón.
        ultima = veredicto.get("ultima_verificacion")
        if ultima:
            contenido.append(
                [
                    Paragraph(
                        f"Última verificación por dispositivo y periodo: <b>{_texto(ultima.get('resultado'))}</b>"
                        f", realizada el {_texto(ultima.get('realizada_en'))} sobre "
                        f"{_texto(ultima.get('device_id'))} del {_texto(ultima.get('desde'))} al "
                        f"{_texto(ultima.get('hasta'))} ({_texto(ultima.get('total_bloques_verificados'))} bloques). "
                        f"Eslabón: <font face='Courier' size='7'>{_texto(ultima.get('hash_eslabon'))}</font>",
                        e["cuerpo"],
                    )
                ]
            )
        elif "ultima_verificacion" in veredicto:
            contenido.append(
                [
                    Paragraph(
                        "No consta una verificación por dispositivo y periodo que cubra este período.",
                        e["nota"],
                    )
                ]
            )
        # HU-26 escenario 4: el veredicto declara qué comprueba y qué no.
        if veredicto.get("alcance"):
            contenido.append([Paragraph(f"<i>{_texto(veredicto['alcance'])}</i>", e["nota"])])
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
            f"El detalle de este reporte está <b>INCOMPLETO</b>. El período solicitado contiene más "
            f"{' y más '.join(recortadas)} de los que admite un solo documento "
            f"(tope de <b>{limite:,}</b> registros por categoría). "
            "El resumen de la sección 1 y el conteo de excursiones críticas sí cubren el período completo, "
            "y todas las excursiones críticas se listan en la sección 5. Para obtener el detalle completo, "
            "divida el período en rangos más cortos y conserve todos los reportes parciales como un único legajo."
        ).replace(",", ".")

        contenido = [
            [Paragraph("<b>⚠ Reporte parcial: faltan registros del período en el detalle</b>", e["cuerpo"])],
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
            # BE-001: una lectura registrada por una persona no es telemetría del
            # nodo; el inspector debe poder distinguirla en el propio documento.
            instante = _fmt_dt(lectura.get("timestamp"))
            if lectura.get("origen") == "manual":
                instante = f"{instante} (manual)"
            filas.append(
                [
                    instante,
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

        # HU-38 esc. 3: ninguna excursión crítica se omite; el tope solo recorta
        # las alertas preventivas más antiguas.
        criticas = [a for a in alertas if a.get("nivel_riesgo") == "excursion_critica"]
        otras = [a for a in alertas if a.get("nivel_riesgo") != "excursion_critica"]
        visibles = criticas + otras[: max(0, MAX_FILAS_ALERTAS - len(criticas))]
        ids_visibles = {id(a) for a in visibles}
        en_orden = [a for a in alertas if id(a) in ids_visibles]

        filas = [["Fecha de apertura", "Dispositivo", "Nivel", "Mensaje", "Estado", "Revisada"]]
        for alerta in en_orden:
            filas.append(
                [
                    _fmt_dt(alerta.get("created_at")),
                    str(alerta.get("device_id") or "—"),
                    _ETIQUETA_RIESGO.get(alerta.get("nivel_riesgo"), str(alerta.get("nivel_riesgo"))),
                    Paragraph(_texto(alerta.get("mensaje")), e["celda"]),
                    "Abierta" if alerta.get("episodio_abierto") else "Cerrada",
                    "Sí" if alerta.get("revisada") else "No",
                ]
            )
        tabla = Table(
            filas, colWidths=[30 * mm, 28 * mm, 26 * mm, 52 * mm, 18 * mm, 18 * mm], repeatRows=1
        )
        tabla.setStyle(_estilo_tabla_base([("ALIGN", (1, 1), (2, -1), "CENTER"), ("ALIGN", (4, 1), (-1, -1), "CENTER")]))
        bloques.append(tabla)
        if len(visibles) < len(alertas):
            bloques.append(Spacer(1, 4))
            bloques.append(
                Paragraph(
                    f"Se muestran todas las excursiones críticas ({len(criticas)}) y las alertas preventivas "
                    f"más recientes hasta completar {MAX_FILAS_ALERTAS}, de {len(alertas)} en el período.",
                    e["nota"],
                )
            )
        return bloques

    def _seccion_acciones(self, acciones: list[dict]) -> list:
        """HU-38 esc. 1: decisión, acción y justificación de cada alerta atendida."""
        e = self._estilos
        bloques = [Spacer(1, 10), Paragraph("Acciones correctivas registradas", e["seccion"])]
        if not acciones:
            bloques.append(Paragraph("No se registraron acciones correctivas en el período.", e["cuerpo"]))
            return bloques
        filas = [["Fecha", "Dispositivo", "Decisión", "Acción ejecutada", "Justificación", "Estado"]]
        for a in acciones:
            filas.append(
                [
                    _fmt_dt(a.get("created_at")),
                    str(a.get("device_id") or "—"),
                    Paragraph(_texto(a.get("decision")), e["celda"]),
                    Paragraph(_texto(a.get("descripcion")), e["celda"]),
                    Paragraph(_texto(a.get("justificacion") or a.get("motivo_rectificacion")), e["celda"]),
                    "Vigente" if a.get("vigente") else "Rectificada",
                ]
            )
        tabla = Table(filas, colWidths=[28 * mm, 26 * mm, 36 * mm, 36 * mm, 28 * mm, 18 * mm], repeatRows=1)
        tabla.setStyle(_estilo_tabla_base([("ALIGN", (1, 1), (1, -1), "CENTER")]))
        bloques.append(tabla)
        return bloques

    def _seccion_equipos(self, equipos: list[dict]) -> list:
        """HU-38 esc. 1 y HU-51 esc. 4: identificación e instalación del equipo
        VIGENTES durante el período (reconstruidas del historial de
        configuración) y la referencia a ese historial."""
        if not equipos:
            return []
        e = self._estilos
        filas = [["Equipo", "Ubicación en el período", "Instalación", "Observaciones"]]
        for d in equipos:
            ubicacion = d.get("ubicacion_periodo", d.get("ubicacion"))
            inicio = d.get("ubicacion_inicio_periodo", ubicacion)
            celda_ubicacion = (
                f"{_texto(inicio)} → {_texto(ubicacion)}" if inicio != ubicacion else _texto(ubicacion)
            )
            fecha = d.get("fecha_instalacion_periodo", d.get("fecha_instalacion"))
            instalador = d.get("instalado_por_nombre")
            filas.append(
                [
                    Paragraph(_texto(d.get("id")), e["celda"]),
                    Paragraph(celda_ubicacion, e["celda"]),
                    Paragraph(
                        _texto(fecha) + (f"<br/>por {_texto(instalador)}" if instalador else ""), e["celda"]
                    ),
                    Paragraph(
                        _texto(d.get("observaciones_instalacion_periodo", d.get("observaciones_instalacion"))),
                        e["celda"],
                    ),
                ]
            )
        tabla = Table(filas, colWidths=[40 * mm, 50 * mm, 26 * mm, 56 * mm], repeatRows=1)
        tabla.setStyle(_estilo_tabla_base([]))
        bloques = [Paragraph("Identificación del equipo e instalación", e["seccion"]), tabla]
        for d in equipos:
            for c in d.get("cambios_ubicacion", []):
                bloques.append(
                    Paragraph(
                        f"{_texto(d.get('id'))}: la ubicación cambió de «{_texto(c.get('valor_anterior'))}» a "
                        f"«{_texto(c.get('valor_nuevo'))}» el {_fmt_dt(c.get('created_at'))}.",
                        e["nota"],
                    )
                )
            if d.get("cambios_posteriores_al_periodo"):
                bloques.append(
                    Paragraph(
                        f"{_texto(d.get('id'))}: la instalación cambió después del período "
                        f"(ubicación actual «{_texto(d.get('ubicacion'))}»); se muestra la vigente en el período.",
                        e["nota"],
                    )
                )
            if "cambios_historial_total" in d:
                bloques.append(
                    Paragraph(
                        f"Historial de configuración de {_texto(d.get('id'))}: "
                        f"{d.get('cambios_historial_total', 0)} cambio(s) registrado(s) con valor anterior, valor "
                        "nuevo, actor y fecha (Dispositivos › Historial de configuración; "
                        f"GET /api/dispositivos/{_texto(d.get('id'))}/historial-configuracion).",
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
                    Paragraph(_texto(str(registro.get("previous_hash", ""))[:32]) + "…", e["hash"]),
                    Paragraph(_texto(str(registro.get("hash_actual", ""))[:32]) + "…", e["hash"]),
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
        bloques = [Paragraph("3. Verificaciones BPA declaradas (checklist)", e["seccion"])]
        if not checklists:
            bloques.append(
                Paragraph(
                    "No se registraron checklists de Buenas Prácticas de Almacenamiento en el período.",
                    e["cuerpo"],
                )
            )
            return bloques

        filas = [["Fecha", "Registrado (UTC)", "Ítems conformes", "Resultado", "Observaciones"]]
        for checklist in checklists:
            conforme = checklist.get("conforme")
            filas.append(
                [
                    str(checklist.get("fecha") or "—"),
                    Paragraph(_registro_checklist(checklist), e["celda"]),
                    f"{checklist.get('total_conformes', 0)} / 10",
                    "Conforme" if conforme else "Con observaciones",
                    Paragraph(_texto(checklist.get("observaciones")), e["celda"]),
                ]
            )
        tabla = Table(filas, colWidths=[22 * mm, 32 * mm, 24 * mm, 32 * mm, 62 * mm], repeatRows=1)
        estilo = _estilo_tabla_base([("ALIGN", (2, 1), (3, -1), "CENTER")])
        for indice, checklist in enumerate(checklists, start=1):
            color = VERDE if checklist.get("conforme") else AMBAR
            estilo.add("TEXTCOLOR", (3, indice), (3, indice), color)
        tabla.setStyle(estilo)
        bloques.append(tabla)
        return bloques

    def _cierre(self) -> list:
        e = self._estilos
        return [
            Spacer(1, 14),
            Paragraph(
                "Este documento fue generado automáticamente por el sistema ColdCare a partir de los "
                "registros almacenados. Su contenido es verificable: la sección 2 declara el resultado de "
                "recomputar la cadena de hashes (SHA-256 / HMAC-SHA256 según la versión de cada eslabón) "
                "sobre la cadena verificada, con su referencia reproducible completa. "
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
        acciones: list[dict] | None = None,
        equipos: list[dict] | None = None,
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
            author="ColdCare",
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
        historia += self._seccion_equipos(equipos or [])
        historia += self._seccion_resumen(estadisticas)
        historia += [KeepTogether(self._seccion_integridad(veredicto_integridad))]
        historia += self._seccion_checklist(checklists)
        historia.append(PageBreak())
        historia += self._seccion_lecturas(lecturas)
        historia.append(PageBreak())
        historia += self._seccion_alertas(alertas)
        historia += self._seccion_acciones(acciones or [])
        historia += self._seccion_trazabilidad(trazabilidad)
        historia += self._cierre()

        doc.build(historia, onFirstPage=self._decorar_pagina, onLaterPages=self._decorar_pagina)
        return buffer.getvalue()
