"""Paper (banco): consolida los resumen.json de todas las corridas en una sola tabla.

Lee evidencia/paper/<CASO>_r<rep>_*/resumen.json y escribe:
    evidencia/paper/tabla_resultados.csv   una fila por corrida (todas las cifras)
    <salida>.md                            tabla por caso (N, media ± DE, mín–máx), lista de
                                           corridas y, al final, evidencia/paper/hallazgos.md

Uso (desde infra-local/):
    python scripts/paper_tabla.py [--salida ../../resultados_pruebas_paper.md]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
EVIDENCIA = RAIZ / "evidencia" / "paper"

# (clave en la fila plana, etiqueta en la tabla)
METRICAS = [
    ("lecturas_guardadas", "Guardadas"),
    ("faltantes", "Faltantes"),
    ("duplicadas_en_base", "Duplicadas"),
    ("reenvios_broker", "Reenvíos en el broker"),
    ("diferidas", "Diferidas"),
    ("fuera_de_orden", "Fuera de orden"),
    ("n_boots", "boot_id distintos"),
    ("corte_s", "Duración del corte (s)"),
    ("vaciado_s", "Vaciado (s)"),
    ("vaciado_reg_s", "Vaciado (registros/s)"),
    ("sin_datos_detectado_s", "sin_datos: último reporte → detección real (s)"),
    ("s_hasta_sin_datos", "sin_datos: último reporte → `abierto_en` nominal (s)"),
    ("arranque_s", "Energía restituida → primera lectura confirmada (s)"),
    ("expuestas", "Expuestas a la falla del COMMIT"),
    ("perdidas_diseno", "Perdidas con el diseño (acuse de aplicación)"),
    ("perdida_solo_puback", "Pérdida estimada solo con PUBACK"),
    ("puerta_deteccion_s", "Puerta: apertura → primera lectura abierta confirmada (s)"),
    ("puerta_prolongada_s", "Puerta: apertura → estado prolongado (≥ 60 s) confirmado (s)"),
    ("puerta_cierre_s", "Puerta: cierre → primera lectura cerrada confirmada (s)"),
    ("puerta_error_duracion_s", "Puerta: duración medida por el nodo − duración marcada (s)"),
    ("alertas_en_ventana", "Alertas abiertas en la corrida"),
    ("hielo_n", "Hielo: lecturas en la ventana estable (últimos 180 s en el baño)"),
    ("hielo_sesgo_c", "Hielo: sesgo medio vs 0.0 °C (°C)"),
    ("hielo_mae_c", "Hielo: error absoluto medio vs 0.0 °C (°C)"),
    ("hielo_max_c", "Hielo: error absoluto máximo (°C)"),
    ("hielo_de_c", "Hielo: desviación estándar en la ventana estable (°C)"),
    ("hielo_t90_s", "Hielo: inmersión → primera captura con 90 % del escalón (s, resolución 10 s)"),
    ("hielo_alerta_recepcion_s", "Excursión < 2 °C: recepción de la lectura → alerta visible (s)"),
    ("hielo_alerta_commit_s", "Excursión < 2 °C: COMMIT de la lectura → alerta visible (s)"),
    ("lat_en_linea_mediana", "Latencia en línea, mediana (s)"),
    ("lat_en_linea_p95", "Latencia en línea, p95 (s)"),
]


def _primer_sin_datos(r: dict, cual: str) -> float | None:
    """Sin corte con diferidas (S12b): el primer `sin_datos` de la corrida, medido desde su último
    reporte. `abierto_en` es nominal (último reporte + umbral); `detectado` es cuando lo abrió la
    tarea periódica."""
    for i in r.get("incidentes") or []:
        if i["tipo"] == "sin_datos" and i.get("ultimo_reporte_utc"):
            if cual == "detectado":
                return i.get("s_silencio_hasta_deteccion")
            a = datetime.fromisoformat(i["abierto_utc"].replace("Z", "+00:00"))
            u = datetime.fromisoformat(i["ultimo_reporte_utc"].replace("Z", "+00:00"))
            return round((a - u).total_seconds(), 1)
    return None


def fila_plana(r: dict) -> dict:
    corte = r.get("corte_inferido") or {}
    lat = r.get("latencia_s") or {}
    return {
        "corrida": r["corrida"], "caso": r["caso"], "repeticion": r["repeticion"],
        "inicio_utc": r["inicio_utc"], "fin_utc": r["fin_utc"],
        "lecturas_guardadas": r["lecturas_guardadas"], "faltantes": r["faltantes"],
        "duplicadas_en_base": r["duplicadas_en_base"], "reenvios_broker": r["reenvios_broker"],
        "diferidas": r.get("diferidas"), "fuera_de_orden": r.get("fuera_de_orden"),
        "n_boots": len(r.get("boots") or {}), "cortes": len(r.get("cortes") or []),
        "corte_s": corte.get("duracion_corte_s"), "vaciado_s": corte.get("vaciado_s"),
        "vaciado_reg_s": corte.get("vaciado_registros_por_s"),
        "s_hasta_sin_datos": corte.get("s_hasta_sin_datos") if corte else _primer_sin_datos(r, "nominal"),
        "sin_datos_detectado_s": (corte.get("s_hasta_sin_datos_detectado") if corte
                                  else _primer_sin_datos(r, "detectado")),
        "arranque_s": next((m.get("s_hasta_lectura") for m in r.get("marcas") or []
                            if m["marca"] == "nodo enchufado"), None),
        "lat_en_linea_n": lat.get("en_linea_n"), "lat_en_linea_mediana": lat.get("en_linea_mediana"),
        "lat_en_linea_p95": lat.get("en_linea_p95"),
        "sin_hora": r.get("sin_hora_llegadas_en_ventana"),
        "expuestas": (r.get("falla_commit") or {}).get("afectadas"),
        "perdidas_diseno": ((r["falla_commit"]["afectadas"] - r["falla_commit"]["afectadas_guardadas"])
                            if r.get("falla_commit") else None),
        "perdida_solo_puback": (r.get("falla_commit") or {}).get("perdida_estimada_solo_puback"),
    }


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def puerta(carpeta: Path) -> dict:
    """S9: tiempos de la puerta a partir de las marcas de la operadora y de lecturas.csv.

    Una lectura con puerta_abierta = t indica que la puerta estuvo abierta en algún momento del
    ciclo; puerta_racha_s es la racha continua de apertura y queda vacía cuando la racha termina.
    Los tiempos se miden hasta el COMMIT (lectura visible en el panel)."""
    marcas = {m["marca"]: _t(m["utc"]) for m in csv.DictReader((carpeta / "marcas.csv").open(encoding="utf-8"))}
    ap, ci = marcas.get("puerta abierta"), marcas.get("puerta cerrada")
    if not ap or not ci:
        return {}
    filas = sorted(csv.DictReader((carpeta / "lecturas.csv").open(encoding="utf-8")),
                   key=lambda f: f["captured_utc"])
    racha = lambda f: float(f["puerta_racha_s"] or 0)
    abierta = next((f for f in filas if _t(f["captured_utc"]) >= ap and f["puerta_abierta"] == "t"), None)
    prolong = next((f for f in filas if _t(f["captured_utc"]) >= ap and racha(f) >= 60), None)
    cierre = next((f for f in filas if _t(f["captured_utc"]) >= ci and racha(f) == 0), None)
    previa = [f for f in filas if cierre and f["captured_utc"] < cierre["captured_utc"] and racha(f) > 0]
    out = {
        "puerta_deteccion_s": round((_t(abierta["commit_utc"]) - ap).total_seconds(), 1) if abierta else None,
        "puerta_prolongada_s": round((_t(prolong["commit_utc"]) - ap).total_seconds(), 1) if prolong else None,
        "puerta_cierre_s": round((_t(cierre["commit_utc"]) - ci).total_seconds(), 1) if cierre else None,
        "puerta_error_duracion_s": None,
    }
    if cierre and previa:
        medida = racha(previa[-1]) + float(cierre["puerta_abierta_s_ciclo"] or 0)
        out["puerta_error_duracion_s"] = round(medida - (ci - ap).total_seconds(), 1)
    return out


def hielo(carpeta: Path) -> dict:
    """HIELO: error de la sonda en el punto de hielo (referencia 0.0 °C) y latencia de la alerta.

    Ventana estable: lecturas capturadas en los 180 s previos a sacar la sonda del baño. t90: desde la
    marca de inmersión hasta la primera captura que recorrió el 90 % del escalón entre la última
    lectura previa y 0 °C (resolución = intervalo de 10 s). Alerta: primera apertura de
    excursion_critica vista por paper_vigia_alertas.py tras la inmersión, contra la recepción y el
    COMMIT de la lectura que la abrió."""
    marcas = {m["marca"]: _t(m["utc"]) for m in csv.DictReader((carpeta / "marcas.csv").open(encoding="utf-8"))}
    ent, sal = marcas.get("sonda en hielo"), marcas.get("sonda al refrigerador")
    if not ent or not sal:
        return {}
    filas = sorted((f for f in csv.DictReader((carpeta / "lecturas.csv").open(encoding="utf-8"))
                    if f["temp_ds18b20"]), key=lambda f: f["captured_utc"])
    temp = lambda f: float(f["temp_ds18b20"])
    estable = [temp(f) for f in filas if 0 <= (sal - _t(f["captured_utc"])).total_seconds() <= 180]
    out: dict = {"hielo_n": len(estable)}
    if estable:
        out.update({"hielo_sesgo_c": round(statistics.mean(estable), 3),
                    "hielo_mae_c": round(statistics.mean(abs(t) for t in estable), 3),
                    "hielo_max_c": round(max(abs(t) for t in estable), 3),
                    "hielo_de_c": round(statistics.stdev(estable), 3) if len(estable) > 1 else None})
    previa = [f for f in filas if _t(f["captured_utc"]) < ent]
    if previa:
        t0 = temp(previa[-1])
        t90 = next((f for f in filas if _t(f["captured_utc"]) >= ent and temp(f) <= 0.1 * t0), None)
        out["hielo_t90_s"] = round((_t(t90["captured_utc"]) - ent).total_seconds(), 1) if t90 else None
    vistas = carpeta / "alertas_vistas.csv"
    if vistas.exists():
        ap = next((e for e in csv.DictReader(vistas.open(encoding="utf-8"))
                   if e["evento"] == "abre" and e["nivel"] == "excursion_critica"
                   and _t(e["visto_utc"]) >= ent and e["received_utc"]), None)
        if ap:
            out["hielo_alerta_recepcion_s"] = round((_t(ap["visto_utc"]) - _t(ap["received_utc"])).total_seconds(), 2)
            out["hielo_alerta_commit_s"] = round((_t(ap["visto_utc"]) - _t(ap["commit_utc"])).total_seconds(), 2)
    return out


def estad(valores: list) -> str:
    v = [float(x) for x in valores if x is not None]
    if not v:
        return "—"
    if len(v) == 1:
        return f"{v[0]:g}"
    media, de = statistics.mean(v), statistics.stdev(v)
    return f"{media:.1f} ± {de:.1f} ({min(v):g}–{max(v):g})"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--salida", type=Path, default=RAIZ.parent.parent / "resultados_pruebas_paper.md")
    args = p.parse_args()

    filas = []
    for ruta in sorted(EVIDENCIA.glob("*_r*_*/resumen.json")):
        fila = fila_plana(json.loads(ruta.read_text(encoding="utf-8")))
        alertas = ruta.parent / "alertas.csv"
        fila["alertas_en_ventana"] = (max(0, sum(1 for _ in alertas.open(encoding="utf-8")) - 1)
                                      if alertas.exists() and alertas.stat().st_size else 0)
        fila.update({k: None for k in ("puerta_deteccion_s", "puerta_prolongada_s", "puerta_cierre_s",
                                       "puerta_error_duracion_s")})
        fila.update({k: None for k, _ in METRICAS if k.startswith("hielo_")})
        if fila["caso"] == "S9":
            fila.update(puerta(ruta.parent))
        if fila["caso"] == "HIELO":
            fila.update(hielo(ruta.parent))
        filas.append(fila)
    if not filas:
        sys.exit("No hay corridas con resumen.json.")

    with (EVIDENCIA / "tabla_resultados.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)

    casos: dict[str, list[dict]] = {}
    for fila in filas:
        casos.setdefault(fila["caso"], []).append(fila)

    md = [
        "# Resultados de las pruebas de banco (generado)", "",
        f"Generado el {datetime.now().strftime('%Y-%m-%d %H:%M')} con `scripts/paper_tabla.py` a partir de "
        "`Backend/infra-local/evidencia/paper/*/resumen.json`. No editar a mano: se regenera. Las cifras "
        "son media ± desviación estándar (mínimo–máximo) entre repeticiones.", "",
        "## Tabla por escenario", "",
        "| Métrica | " + " | ".join(f"{c} (N={len(v)})" for c, v in casos.items()) + " |",
        "|---|" + "---|" * len(casos),
    ]
    for clave, etiqueta in METRICAS:
        md.append(f"| {etiqueta} | " + " | ".join(estad([f[clave] for f in v]) for v in casos.values()) + " |")

    md += ["", "## Corridas", "",
           "| Corrida | Guardadas | Faltantes | Duplicadas | Reenvíos | Diferidas | Fuera de orden | boots | Cortes "
           "| Corte (s) | Vaciado (s) | sin_datos real (s) | Lat. en línea mediana (s) | Sin hora |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for f in filas:
        md.append(
            f"| {f['corrida']} | {f['lecturas_guardadas']} | {f['faltantes']} | {f['duplicadas_en_base']} | "
            f"{f['reenvios_broker']} | {f['diferidas']} | {f['fuera_de_orden']} | {f['n_boots']} | {f['cortes']} | "
            f"{f['corte_s']} | {f['vaciado_s']} | {f['sin_datos_detectado_s']} | {f['lat_en_linea_mediana']} | "
            f"{f['sin_hora']} |")

    hallazgos = EVIDENCIA / "hallazgos.md"
    if hallazgos.exists():
        md += ["", hallazgos.read_text(encoding="utf-8").strip()]
    args.salida.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Escrito {args.salida} y {EVIDENCIA / 'tabla_resultados.csv'} ({len(filas)} corridas).")


if __name__ == "__main__":
    main()
