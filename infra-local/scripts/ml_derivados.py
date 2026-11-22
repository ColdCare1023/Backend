"""IA (banco): derivados y figuras de las corridas de evidencia/ml/ para la tesis y el paper.

    python scripts/ml_derivados.py <evidencia/ml> <salida>

Lee cada corrida cerrada (corrida.json con fin_utc, lecturas.csv, marcas.csv, alertas.csv; las de
_anuladas/ no entran) y escribe en <salida>/derivados/ y <salida>/figuras/:
  resumen_corridas.csv        una fila por corrida de excursión (S-P1/3/5/10/7, S-C)
  resumen_operacion_normal.csv una fila por corrida S-N
  episodios_fuera_de_rango.csv cada tramo continuo fuera de 2-8 °C, en cualquier corrida
  resumen_por_escenario.csv   n, excursiones, pico y tiempos por escenario
  fig_ia_*.png / .pdf         figuras a 300 dpi, ancho de columna IEEE
La hora de la acción es la real del MC-38 (primera lectura con puerta abierta cerca de la marca
programada); si el MC-38 no marca nada cerca, se usa la marca. Necesita matplotlib.
"""

import csv
import json
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ML, OUT = Path(sys.argv[1]), Path(sys.argv[2])
(OUT / "derivados").mkdir(parents=True, exist_ok=True)
(OUT / "figuras").mkdir(parents=True, exist_ok=True)
LIMA = timezone(timedelta(hours=-5))
ESC_ORDEN = ["SP1", "SP3", "SP5", "SP7", "SP10", "SC"]


def t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def lima(d):
    return d.astimezone(LIMA).strftime("%Y-%m-%d %H:%M:%S") if d else ""


def r2(x):
    return "" if x is None else round(x, 2)


def mins(a, b):
    return None if a is None or b is None else (b - a).total_seconds() / 60


def cargar(carpeta):
    datos = json.loads((carpeta / "corrida.json").read_text(encoding="utf-8"))
    if not datos.get("fin_utc"):
        return None
    lect = []
    with (carpeta / "lecturas.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r["captured_utc"] or not r["temp_ds18b20"]:
                continue
            lect.append((t(r["captured_utc"]), float(r["temp_ds18b20"]), r.get("puerta_abierta") == "t"))
    lect.sort()
    marcas = []
    if (carpeta / "marcas.csv").exists():
        with (carpeta / "marcas.csv").open(encoding="utf-8") as f:
            marcas = [(t(r["utc"]), r["marca"]) for r in csv.DictReader(f)]
    alertas = []
    if (carpeta / "alertas.csv").exists():
        with (carpeta / "alertas.csv").open(encoding="utf-8") as f:
            alertas = sorted(t(r["creada_utc"]) for r in csv.DictReader(f) if r.get("creada_utc"))
    res = json.loads((carpeta / "resumen.json").read_text(encoding="utf-8")) if (carpeta / "resumen.json").exists() else {}
    return {"nombre": carpeta.name, "caso": datos["caso"], "rep": datos["repeticion"], "device": datos["device_id"],
            "ini": t(datos["inicio_utc"]), "fin": t(datos["fin_utc"]), "lect": lect, "marcas": marcas,
            "alertas": alertas, "res": res}


def racha_puerta(lect, cerca, antes_s=180, despues_s=300):
    """Primera racha de puerta abierta que empieza en [cerca - antes_s, cerca + despues_s]: (inicio, fin)."""
    ini = fin = None
    for ts, _, p in lect:
        if ini is None:
            if p and cerca - timedelta(seconds=antes_s) <= ts <= cerca + timedelta(seconds=despues_s):
                ini = ts
        elif not p:
            fin = ts  # primera lectura ya cerrada
            break
    return ini, fin


def episodios(c):
    """Tramos fuera de 2-8 °C; dos tramos del mismo tipo separados por ≤ 60 s en rango son uno solo
    (la sonda oscila ±1 paso de 0,0625 °C alrededor del límite)."""
    out, cur = [], None
    for ts, v, _ in c["lect"]:
        fuera = "alta" if v > 8 else "baja" if v < 2 else None
        if not fuera:
            continue
        if cur and cur["tipo"] == fuera and ts - cur["fin"] <= timedelta(seconds=70):
            cur["fin"], cur["n"] = ts, cur["n"] + 1
            cur["ext"] = max(cur["ext"], v) if fuera == "alta" else min(cur["ext"], v)
        else:
            if cur:
                out.append(cur)
            cur = {"tipo": fuera, "ini": ts, "fin": ts, "n": 1, "ext": v}
    if cur:
        out.append(cur)
    return out


corridas = [c for p in sorted(ML.iterdir()) if p.is_dir() and not p.name.startswith("_")
            and (p / "corrida.json").exists() and (c := cargar(p))]
exc, normales, curvas = [], [], defaultdict(list)

for c in corridas:
    lect, res = c["lect"], c["res"]
    if c["caso"] == "SN":
        v = [x for _, x, _ in lect]
        aperturas = sum(1 for a, b in pairwise(lect) if b[2] and not a[2]) + (1 if lect and lect[0][2] else 0)
        eps = episodios(c)
        normales.append({
            "corrida": c["nombre"], "inicio_lima": lima(c["ini"]), "fin_lima": lima(c["fin"]),
            "horas": round((c["fin"] - c["ini"]).total_seconds() / 3600, 2), "lecturas": len(lect),
            "faltantes": res.get("faltantes", ""), "diferidas": res.get("diferidas", ""),
            "t_min_c": min(v), "t_max_c": max(v), "t_media_c": round(st.mean(v), 2),
            "lecturas_bajo_2c": sum(x < 2 for x in v), "lecturas_sobre_8c": sum(x > 8 for x in v),
            "episodios_bajos": sum(e["tipo"] == "baja" for e in eps),
            "episodios_altos": sum(e["tipo"] == "alta" for e in eps),
            "alertas": len(c["alertas"]), "aperturas_puerta": aperturas,
        })
        continue
    accion = next((m for m in c["marcas"] if m[1].startswith(("puerta abierta", "carga tibia dentro"))), None)
    if not accion:
        continue
    t_prog = accion[0]
    a_ini, a_fin = racha_puerta(lect, t_prog)
    fuente = "MC-38" if a_ini else "marca"
    a_ini = a_ini or t_prog
    if c["caso"] == "SC":  # la carga termina cuando se cierra la puerta tras sacarla
        fuera = next((m for m in c["marcas"] if m[1].startswith("carga tibia fuera")), None)
        a_fin = racha_puerta(lect, fuera[0], despues_s=600)[1] if fuera else None
        a_fin = a_fin or (fuera[0] if fuera else None)
    elif a_fin is None:
        cierre = next((m for m in c["marcas"] if m[1].startswith("puerta cerrada")), None)
        a_fin = cierre[0] if cierre else None
    base = [x for ts, x, _ in lect if a_ini - timedelta(minutes=20) <= ts < a_ini]
    despues = [(ts, x) for ts, x, _ in lect if ts >= a_ini]
    pico_ts, pico = max(despues, key=lambda p: p[1])
    cruce = next((ts for ts, x in despues if x > 8), None)
    alerta = next((a for a in c["alertas"] if a >= a_ini - timedelta(minutes=10)), None)
    exc.append({
        "corrida": c["nombre"], "escenario": c["caso"], "device_id": c["device"],
        "accion_inicio_lima": lima(a_ini), "accion_fin_lima": lima(a_fin), "fuente_hora": fuente,
        "accion_min": r2(mins(a_ini, a_fin)), "base_media_c": r2(st.mean(base)) if base else "",
        "base_min_c": min(base) if base else "", "base_max_c": max(base) if base else "",
        "pico_c": pico, "min_hasta_pico": r2(mins(a_ini, pico_ts)),
        "min_hasta_8c": r2(mins(a_ini, cruce)), "min_sobre_8c": round(sum(x > 8 for _, x in despues) * 10 / 60, 2),
        "alerta_lima": lima(alerta), "s_accion_a_alerta": round((alerta - a_ini).total_seconds()) if alerta else "",
        "resultado": "excursion_alta" if pico > 8 else "negativo_dificil",
        "lecturas": len(lect), "faltantes": res.get("faltantes", ""), "duplicadas": res.get("duplicadas_en_base", ""),
    })
    curvas[c["caso"]].append([(mins(a_ini, ts), x) for ts, x, _ in lect])

with (OUT / "derivados" / "resumen_corridas.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(exc[0]))
    w.writeheader()
    w.writerows(sorted(exc, key=lambda r: (ESC_ORDEN.index(r["escenario"]), r["accion_inicio_lima"])))
with (OUT / "derivados" / "resumen_operacion_normal.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(normales[0]))
    w.writeheader()
    w.writerows(normales)
with (OUT / "derivados" / "episodios_fuera_de_rango.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["corrida", "tipo", "inicio_lima", "fin_lima", "duracion_min", "lecturas", "extremo_c"])
    for c in corridas:
        for e in episodios(c):
            w.writerow([c["nombre"], e["tipo"], lima(e["ini"]), lima(e["fin"]),
                        round((e["fin"] - e["ini"]).total_seconds() / 60 + 10 / 60, 2), e["n"], e["ext"]])
with (OUT / "derivados" / "resumen_por_escenario.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["escenario", "corridas", "excursiones_altas", "negativos_dificiles", "pico_mediana_c", "pico_min_c",
                "pico_max_c", "min_hasta_8c_mediana", "s_accion_a_alerta_mediana"])
    for e in ESC_ORDEN:
        rs = [r for r in exc if r["escenario"] == e]
        if not rs:
            continue
        pk = [r["pico_c"] for r in rs]
        m8 = [r["min_hasta_8c"] for r in rs if r["min_hasta_8c"] != ""]
        sa = [r["s_accion_a_alerta"] for r in rs if r["s_accion_a_alerta"] != "" and r["s_accion_a_alerta"] >= 0]
        w.writerow([e, len(rs), sum(r["resultado"] == "excursion_alta" for r in rs),
                    sum(r["resultado"] == "negativo_dificil" for r in rs), r2(st.median(pk)), min(pk), max(pk),
                    r2(st.median(m8)) if m8 else "", round(st.median(sa)) if sa else ""])

# Figuras
BLUE, INK, MUTED, BAND, SOFT = "#2a78d6", "#0b0b0b", "#898781", "#ecebe8", "#52514e"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.color": "#e6e5e1", "grid.linewidth": .5,
                     "savefig.dpi": 300, "savefig.bbox": "tight", "figure.facecolor": "white"})
NOMBRE = {"SP1": "Door open 1 min", "SP3": "Door open 3 min", "SP5": "Door open 5 min",
          "SP7": "Door open 7 min", "SP10": "Door open 10 min", "SC": "Warm load 20 min"}


def guardar(fig, nombre):
    for ext in ("png", "pdf"):
        fig.savefig(OUT / "figuras" / f"{nombre}.{ext}")
    plt.close(fig)


fig, axs = plt.subplots(2, 3, figsize=(7.16, 3.6), sharex=True, sharey=True)
for ax, e in zip(axs.flat, ESC_ORDEN, strict=False):
    ax.axhspan(2, 8, color=BAND, zorder=0, lw=0)
    for cv in curvas.get(e, []):
        xs = [x for x, _ in cv if -20 <= x <= 60]
        ys = [y for x, y in cv if -20 <= x <= 60]
        ax.plot(xs, ys, color=BLUE, lw=.9, alpha=.75)
    ax.axvline(0, color=MUTED, lw=.6, ls="--")
    n = len(curvas.get(e, []))
    ax.set_title(f"{NOMBRE[e]} (n = {n})", fontsize=8, color=INK, loc="left")
    ax.set_xlim(-20, 60)
    ax.set_ylim(0, 20)
for ax in axs[1]:
    ax.set_xlabel("Minutes from door opening / load")
for ax in axs[:, 0]:
    ax.set_ylabel("DS18B20 (°C)")
axs[0, 0].text(58, 7.6, "2–8 °C", ha="right", va="top", color=SOFT, fontsize=7)
fig.tight_layout()
guardar(fig, "fig_ia_curvas_por_escenario")

fig, ax = plt.subplots(figsize=(3.5, 2.0))
ax.axhline(8, color=MUTED, lw=.8, ls="--")
ax.text(5.45, 8.25, "8 °C limit", ha="right", va="bottom", color=SOFT, fontsize=7)
for i, e in enumerate(ESC_ORDEN):
    rs = [r for r in exc if r["escenario"] == e]
    for j, r in enumerate(rs):
        dx = (j - (len(rs) - 1) / 2) * .09
        lleno = r["resultado"] == "excursion_alta"
        ax.scatter(i + dx, r["pico_c"], s=16, color=BLUE if lleno else "white", edgecolor=BLUE, lw=.9, zorder=3)
ax.set_xticks(range(len(ESC_ORDEN)), ["P1", "P3", "P5", "P7", "P10", "Load"])
ax.set_xlim(-.6, len(ESC_ORDEN) - .4)
ax.set_ylabel("Peak temperature (°C)")
ax.set_xlabel("Scenario (open dot = no excursion)")
guardar(fig, "fig_ia_pico_por_escenario")

fig, ax = plt.subplots(figsize=(3.5, 2.0))
for i, e in enumerate(ESC_ORDEN):
    rs = [r for r in exc if r["escenario"] == e and r["s_accion_a_alerta"] != "" and r["s_accion_a_alerta"] >= 0]
    for j, r in enumerate(rs):
        dx = (j - (len(rs) - 1) / 2) * .09
        ax.scatter(i + dx, r["s_accion_a_alerta"] / 60, s=16, color=BLUE, zorder=3)
ax.set_xticks(range(len(ESC_ORDEN)), ["P1", "P3", "P5", "P7", "P10", "Load"])
ax.set_xlim(-.6, len(ESC_ORDEN) - .4)
ax.set_ylim(bottom=0)
ax.set_ylabel("Door opening → alert (min)")
ax.set_xlabel("Scenario")
guardar(fig, "fig_ia_tiempo_hasta_alerta")

sn = [c for c in corridas if c["caso"] == "SN"]
fig, axs = plt.subplots(len(sn), 1, figsize=(3.5, 0.95 * len(sn) + .4), sharex=True, sharey=True)
for ax, c in zip(axs, sn, strict=False):
    h0 = c["ini"]
    ax.axhspan(2, 8, color=BAND, zorder=0, lw=0)
    ax.plot([(ts - h0).total_seconds() / 3600 for ts, _, _ in c["lect"]], [x for _, x, _ in c["lect"]],
            color=BLUE, lw=.6)
    ax.text(.99, .97, f"{c['nombre'].split('_')[0]} r{c['rep']} · start {lima(c['ini'])[5:16]}",
            transform=ax.transAxes, ha="right", va="top", color=SOFT, fontsize=6.5)
    ax.set_ylim(-3, 12)
    ax.set_yticks([0, 4, 8])
axs[-1].set_xlabel("Hours from start")
fig.supylabel("DS18B20 (°C)", fontsize=8, color=INK)
fig.tight_layout()
guardar(fig, "fig_ia_operacion_normal")

print(f"{len(exc)} corridas de excursión, {len(normales)} S-N -> {OUT}")
