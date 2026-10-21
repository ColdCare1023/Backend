"""Figuras y documentos Markdown generados a partir de los resultados.

matplotlib es una dependencia SOLO del entorno offline de entrenamiento
(requirements-ml.txt); este módulo nunca se importa desde el servicio.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve

from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva

COLOR_TEMP = "#1f4e79"
COLOR_EPISODIO = "#d62728"
COLOR_PUERTA = "#7f7f7f"
COLORES_MODELOS = {
    "random_forest": "#1f4e79",
    "proximidad_limite": "#ff7f0e",
    "tendencia_reciente": "#2ca02c",
    "regla_legada": "#9467bd",
}


def _guardar(fig, ruta: Path) -> None:
    fig.tight_layout()
    fig.savefig(ruta, dpi=130)
    plt.close(fig)


def _local(ts: pd.Series, horas: int) -> pd.Series:
    return (ts + pd.Timedelta(hours=horas)).dt.tz_localize(None)


def _sombrear_episodios(ax, episodios: pd.DataFrame, horas: int) -> None:
    for _, ep in episodios.iterrows():
        ini = pd.to_datetime(ep["inicio_ns"], unit="ns") + pd.Timedelta(hours=horas)
        fin = pd.to_datetime(ep["fin_ns"], unit="ns") + pd.Timedelta(hours=horas, minutes=1)
        ax.axvspan(ini, fin, color=COLOR_EPISODIO, alpha=0.15, lw=0)


def figura_series_hohenheim(rutas, hohenheim, config: ConfiguracionPreventiva) -> None:
    from src.infrastructure.ai.prediccion_preventiva.experimentos import (
        CORTE_PRUEBA_LOCAL,
        CORTES_WALK_FORWARD_LOCAL,
    )

    series = sorted(hohenheim.rejillas["serie_id"].unique())
    fig, ejes = plt.subplots(len(series), 1, figsize=(13, 1.7 * len(series)), sharex=True)
    aperturas = hohenheim.acciones[hohenheim.acciones["accion"] == "Door opened"]
    for ax, serie in zip(np.atleast_1d(ejes), series, strict=True):
        r = hohenheim.rejillas[hohenheim.rejillas["serie_id"] == serie]
        ax.plot(_local(r["timestamp"], 2), r["temperatura"], color=COLOR_TEMP, lw=0.6)
        _sombrear_episodios(ax, hohenheim.episodios[hohenheim.episodios["serie_id"] == serie], 2)
        for _, a in aperturas.iterrows():
            ax.axvspan(_local(pd.Series([a["inicio"]]), 2)[0], _local(pd.Series([a["fin"]]), 2)[0], color=COLOR_PUERTA, alpha=0.12, lw=0)
        for y in (config.rango.minimo_celsius, config.rango.maximo_celsius):
            ax.axhline(y, color="k", lw=0.5, ls=":")
        for c in CORTES_WALK_FORWARD_LOCAL:
            ax.axvline(pd.Timestamp(c), color="#555", lw=0.7, ls="--")
        ax.axvline(pd.Timestamp(CORTE_PRUEBA_LOCAL), color="k", lw=1.2)
        ax.set_ylabel(f"{serie}\n°C", fontsize=8)
        ax.set_ylim(4, 16)
    np.atleast_1d(ejes)[0].set_title(
        "Hohenheim — sensores incluidos (rejilla 60 s). Rojo: episodios >8 °C; gris: puerta abierta (protocolo); "
        "discontinuas: cortes de validación; negra: inicio de prueba", fontsize=9
    )
    np.atleast_1d(ejes)[-1].set_xlabel("Hora local (CEST)")
    _guardar(fig, rutas.artefactos / "fig_hohenheim_series.png")


def figura_series_hospital(rutas, hospital, config: ConfiguracionPreventiva) -> None:
    from src.infrastructure.ai.prediccion_preventiva.experimentos import DESFASE_LOCAL_H

    horas = DESFASE_LOCAL_H["hospital"]
    series = sorted(hospital.rejillas["serie_id"].unique())
    fig, ejes = plt.subplots(len(series), 1, figsize=(13, 2.2 * len(series)), sharex=True)
    for ax, serie in zip(np.atleast_1d(ejes), series, strict=True):
        r = hospital.rejillas[hospital.rejillas["serie_id"] == serie]
        ax.plot(_local(r["timestamp"], horas), r["temperatura"], color=COLOR_TEMP, lw=0.6)
        _sombrear_episodios(ax, hospital.episodios[hospital.episodios["serie_id"] == serie], horas)
        crudas = hospital.lecturas[(hospital.lecturas["serie_id"] == serie) & (hospital.lecturas["puerta_abierta"] == 1)]
        ax.scatter(_local(crudas["timestamp"], horas), np.full(len(crudas), 9.5), marker="v", color=COLOR_PUERTA, s=18, label="puerta abierta")
        for y in (config.rango.minimo_celsius, config.rango.maximo_celsius):
            ax.axhline(y, color="k", lw=0.5, ls=":")
        ax.set_ylabel(f"{serie}\n°C", fontsize=8)
        ax.set_ylim(2, 10.5)
    np.atleast_1d(ejes)[0].set_title("Hospital — refrigeradores compatibles (ULT26-001 excluido). Rojo: episodios", fontsize=9)
    np.atleast_1d(ejes)[-1].set_xlabel("Hora local supuesta (UTC-5)")
    _guardar(fig, rutas.artefactos / "fig_hospital_series.png")


def figura_pr(rutas, a1: dict) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    for nombre, curva in a1["curvas_pr_prueba"].items():
        if curva is None:
            continue
        ap = a1["prueba"][nombre]["observacion"]["average_precision"]
        ax.plot(curva["recall"], curva["precision"], label=f"{nombre} (AP={ap:.3f})", color=COLORES_MODELOS.get(nombre))
    prev = a1["prueba"]["random_forest"]["observacion"]["prevalencia"]
    ax.axhline(prev, color="k", ls=":", lw=0.8, label=f"prevalencia={prev:.3f}")
    ax.set_xlabel("Recall (riesgo_preventivo)")
    ax.set_ylabel("Precisión")
    ax.set_title("Curva precisión–recall en prueba (A1, 09–11/09)", fontsize=10)
    ax.legend(fontsize=8)
    _guardar(fig, rutas.artefactos / "fig_pr_prueba.png")


def figura_importancias(rutas, a1: dict) -> None:
    perm = pd.Series(a1["importancias"]["permutacion_prueba_media"])
    std = pd.Series(a1["importancias"]["permutacion_prueba_std"])
    imp = pd.Series(a1["importancias"]["impureza"])
    orden = perm.sort_values(ascending=True).index[-15:]
    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    a.barh(orden, perm[orden], xerr=std[orden], color=COLOR_TEMP)
    a.set_title("Permutación en prueba (ΔAP)", fontsize=10)
    b.barh(orden, imp[orden], color="#999")
    b.set_title("Impureza (entrenamiento)", fontsize=10)
    _guardar(fig, rutas.artefactos / "fig_importancias.png")


def figura_anticipacion(rutas, a1: dict) -> None:
    anticip = a1["prueba"]["random_forest"]["evento"]["anticipacion_min"]
    fig, ax = plt.subplots(figsize=(6, 4))
    if isinstance(anticip, dict) and anticip.get("valores"):
        ax.hist(anticip["valores"], bins=range(0, 32, 2), color=COLOR_TEMP, edgecolor="white")
    ax.set_xlabel("Anticipación antes de la primera salida de rango (min)")
    ax.set_ylabel("Episodios por sensor")
    ax.set_title("Distribución de la anticipación (A1, prueba)", fontsize=10)
    _guardar(fig, rutas.artefactos / "fig_anticipacion.png")


def figura_calibracion(rutas, a1: dict) -> None:
    obs = a1["_obs"]
    idx = a1["_indices"]["prueba"]
    y = obs.y[idx]
    p = a1["_puntuaciones"]["prueba_rf"]
    fig, ax = plt.subplots(figsize=(5, 5))
    if 0 < y.sum() < y.size:
        frac, media = calibration_curve(y, p, n_bins=10, strategy="quantile")
        ax.plot(media, frac, marker="o", color=COLOR_TEMP, label="Random Forest (no calibrado)")
    ax.plot([0, 1], [0, 1], "k:", lw=0.8)
    ax.set_xlabel("Puntuación media del bin")
    ax.set_ylabel("Fracción observada de riesgo_preventivo")
    ax.set_title("Diagrama de fiabilidad (prueba A1)", fontsize=10)
    ax.legend(fontsize=8)
    _guardar(fig, rutas.artefactos / "fig_calibracion.png")


def figura_ejemplo(rutas, a1: dict, hohenheim, serie: str = "SENSOR06", dia: str = "2024-09-10") -> None:
    obs = a1["_obs"]
    idx = a1["_indices"]["prueba"]
    tabla = obs.tabla.iloc[idx].assign(p=a1["_puntuaciones"]["prueba_rf"])
    sub = tabla[(tabla["serie_id"] == serie) & (tabla["dia_local"] == dia)]
    if sub.empty:
        return
    umbral = a1["umbral_random_forest"]["umbral"]
    r = hohenheim.rejillas[hohenheim.rejillas["serie_id"] == serie]
    r = r[_local(r["timestamp"], 2).dt.strftime("%Y-%m-%d") == dia]
    eps = hohenheim.episodios[
        (hohenheim.episodios["serie_id"] == serie)
        & (hohenheim.episodios["fin_ns"] >= r["g_ns"].min())
        & (hohenheim.episodios["inicio_ns"] <= r["g_ns"].max())
    ]
    fig, (a, b) = plt.subplots(2, 1, figsize=(13, 5), sharex=True)
    a.plot(_local(r["timestamp"], 2), r["temperatura"], color=COLOR_TEMP, lw=0.8)
    a.axhline(8, color="k", ls=":", lw=0.6)
    _sombrear_episodios(a, eps, 2)
    a.set_ylabel("°C")
    a.set_title(f"{serie}, {dia} (prueba): temperatura y puntuación del Random Forest", fontsize=10)
    t = _local(sub["timestamp"], 2)
    b.scatter(t, sub["p"], s=3, c=np.where(sub["y"] == 1, COLOR_EPISODIO, COLOR_TEMP))
    b.axhline(umbral, color="k", ls="--", lw=0.8, label=f"umbral={umbral:.3f}")
    _sombrear_episodios(b, eps, 2)
    b.set_ylabel("puntuación RF")
    b.set_xlabel("Hora local (CEST). Rojo: instantes con excursión en ≤30 min")
    b.legend(fontsize=8)
    _guardar(fig, rutas.artefactos / "fig_ejemplo_prueba.png")


def figura_transferencia(rutas, c: dict, hospital, umbral: float) -> None:
    if not c.get("viable"):
        return
    from src.infrastructure.ai.prediccion_preventiva.experimentos import DESFASE_LOCAL_H

    horas = DESFASE_LOCAL_H["hospital"]
    obs = c["_obs"]
    tabla = obs.tabla.assign(p=c["_p"])
    fig, ejes = plt.subplots(2, 1, figsize=(13, 5), sharex=True)
    for serie in sorted(tabla["serie_id"].unique()):
        sub = tabla[tabla["serie_id"] == serie]
        ejes[0].plot(_local(sub["timestamp"], horas), sub["temperatura"], lw=0.6, label=serie)
        ejes[1].plot(_local(sub["timestamp"], horas), sub["p"], lw=0.5, label=serie)
    _sombrear_episodios(ejes[0], hospital.episodios, horas)
    ejes[0].axhline(8, color="k", ls=":", lw=0.6)
    ejes[0].set_ylabel("°C")
    ejes[0].legend(fontsize=8)
    ejes[1].axhline(umbral, color="k", ls="--", lw=0.8)
    ejes[1].set_ylabel("puntuación RF")
    ejes[0].set_title("Experimento C: modelo de Hohenheim aplicado sin reentrenar al hospital", fontsize=10)
    _guardar(fig, rutas.artefactos / "fig_transferencia.png")


def generar_figuras(rutas, hohenheim, hospital, a1: dict, c: dict, config: ConfiguracionPreventiva) -> None:
    """Figuras de resultados (las de series las genera la auditoría)."""
    figura_pr(rutas, a1)
    figura_importancias(rutas, a1)
    figura_anticipacion(rutas, a1)
    figura_calibracion(rutas, a1)
    figura_ejemplo(rutas, a1, hohenheim)
    figura_transferencia(rutas, c, hospital, a1["umbral_random_forest"]["umbral"])


# ── Markdown ─────────────────────────────────────────────────────


def _fmt(v, dec: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, dict):
        if v.get("estado") == "no estimable":
            return f"no estimable ({v.get('motivo')})"
        if "valor" in v:
            return _fmt(v["valor"], dec)
    if isinstance(v, bool):
        return "sí" if v else "no"
    if isinstance(v, float):
        return f"{v:.{dec}f}"
    return str(v)


def _tabla(filas: list[dict], columnas: list[str]) -> str:
    cab = "| " + " | ".join(columnas) + " |\n|" + "|".join("---" for _ in columnas) + "|\n"
    return cab + "".join("| " + " | ".join(_fmt(f.get(c)) for c in columnas) + " |\n" for f in filas)


def generar_markdown_auditoria(rutas, auditoria: dict, hohenheim, hospital) -> None:
    h, s = auditoria["hohenheim"], auditoria["hospital"]
    partes = [
        "# Auditoría de datasets (generado)\n",
        "> Archivo generado por `python -m src.infrastructure.ai.prediccion_preventiva auditar`. "
        "No editar a mano: la versión comentada está en `01_DATASET_AUDIT.md`. "
        f"Generado: {auditoria['generado_en']}.\n",
        "## Archivos de entrada y hashes SHA-256\n",
        _tabla(auditoria["entradas"], ["archivo", "fuente", "bytes", "sha256"]),
        "\n## Configuración\n",
        "```json\n" + json.dumps(auditoria["configuracion"], indent=2, ensure_ascii=False) + "\n```\n",
    ]
    for etiqueta, a in (("Hohenheim", h), ("Hospital", s)):
        p = a["procedencia"]
        partes += [
            f"\n## {etiqueta}\n",
            f"- Título: {p['titulo']}\n- Autores: {p['autores']}\n- DOI: {p['doi']}\n- Licencia: {p['licencia']}\n"
            f"- Publicado: {p['publicado']}\n- Sensor: {p['sensor']}\n- Esquema: {a['esquema_original']}\n"
            f"- Zona horaria: {a['zona_horaria']}\n",
            f"\n### Importación\n\n```json\n{json.dumps(a['importacion'], indent=2, ensure_ascii=False)}\n```\n",
        ]
        perfiles = a.get("perfil_periodo_por_sensor") or a["perfil_por_equipo"]
        filas = [{"serie": k, **v} for k, v in perfiles.items()]
        partes += [
            "\n### Perfil por serie\n\n",
            _tabla(filas, ["serie", "registros", "temperaturas_validas", "inicio_utc", "fin_utc", "duracion_h",
                           "intervalo_mediana_s", "huecos_mayores_hueco_max", "hueco_max_s", "timestamps_duplicados",
                           "temp_min", "temp_mediana", "temp_max", "pct_en_rango", "pct_sobre_max", "pct_bajo_min",
                           "clase_equipo"]),
            "\n### Decisiones de inclusión\n\n",
            _tabla([{"serie": k, **v} for k, v in a["decisiones_inclusion"].items()], ["serie", "incluido", "motivo", "detalle"]),
            "\n### Excursiones y eventos (series incluidas)\n\n",
            _tabla([{"serie": k, **v} for k, v in a["eventos"]["por_serie"].items()],
                   ["serie", "episodios", "inicio_no_observado", "altas", "bajas", "duracion_mediana_min", "duracion_max_min"]),
            f"\n- Episodios: {a['eventos']['episodios_total']} (altos {a['eventos']['episodios_altos']}, bajos "
            f"{a['eventos']['episodios_bajos']}; con salida de rango no observada: {a['eventos']['episodios_inicio_no_observado']}).\n"
            f"- Incidentes independientes con salida de rango observada (entorno físico): "
            f"**{a['eventos']['incidentes_independientes']}** (más {a['eventos']['incidentes_solo_arranque_en_caliente']} "
            "formados solo por arranques en caliente).\n"
            f"- Estados de etiqueta (H = 30 min): `{a['eventos']['estados_etiqueta']}`.\n",
        ]
    incidentes = pd.read_csv(rutas.artefactos / "incidentes_hohenheim.csv")
    incidentes["inicio_local"] = (pd.to_datetime(incidentes["inicio_ns"], unit="ns") + pd.Timedelta(hours=2)).astype(str)
    incidentes["fin_local"] = (pd.to_datetime(incidentes["fin_ns"], unit="ns") + pd.Timedelta(hours=2)).astype(str)
    partes += [
        "\n## Incidentes independientes de Hohenheim (hora local CEST)\n\n",
        _tabla(
            incidentes.to_dict(orient="records"),
            ["incidente_id", "inicio_local", "fin_local", "series", "tipo", "pico_c", "inicio_observado"],
        ),
        "\n## Figuras\n\n![Hohenheim](artefactos/fig_hohenheim_series.png)\n\n![Hospital](artefactos/fig_hospital_series.png)\n",
    ]
    (rutas.docs / "DATASET_AUDIT.md").write_text("".join(partes), encoding="utf-8")


def _fila_modelo(nombre: str, r: dict) -> dict:
    if "observacion" not in r:
        return {"modelo": nombre, "AP": r.get("motivo")}
    o, e = r["observacion"], r["evento"]
    pc = o["por_clase"]["riesgo_preventivo"]
    return {
        "modelo": nombre,
        "umbral": o["umbral"],
        "AP": o["average_precision"],
        "ROC-AUC": o["roc_auc"],
        "precision_riesgo": pc["precision"],
        "recall_riesgo": pc["recall"],
        "F1_riesgo": pc["f1"],
        "macro_F1": o["macro_f1"],
        "bal_acc": o["balanced_accuracy"],
        "accuracy": o["accuracy"],
        "episodios_anticipados": f"{e['episodios_anticipados']}/{e['episodios_evaluables']}",
        "ep_con_5min": e["sensibilidad_por_episodio_min_5min"],
        "ep_con_10min": e["sensibilidad_por_episodio_min_10min"],
        "incidentes_anticipados": f"{e['incidentes_anticipados']}/{e['incidentes_evaluables']}",
        "anticipacion_mediana_min": e["anticipacion_min"].get("mediana") if isinstance(e["anticipacion_min"], dict) else None,
        "falsas_alarmas_dia_sensor": e["falsas_alarmas_por_dia_sensor"],
    }


COLUMNAS_MODELO = [
    "modelo", "umbral", "AP", "ROC-AUC", "precision_riesgo", "recall_riesgo", "F1_riesgo", "macro_F1", "bal_acc",
    "accuracy", "episodios_anticipados", "ep_con_5min", "ep_con_10min", "incidentes_anticipados",
    "anticipacion_mediana_min", "falsas_alarmas_dia_sensor",
]
COLUMNAS_CRITERIOS = ["criterio", "valor", "valor_rf", "valor_baseline", "mejor_baseline", "umbral", "cumple"]


def _seccion_transferencia(titulo: str, c: dict) -> list[str]:
    partes = [f"\n## {titulo}\n\n"]
    if not c.get("viable"):
        return [*partes, f"No viable: {c.get('motivo')}\n"]
    return [
        *partes,
        f"Series: {c['series']}; {c['observaciones']} obs, {c['positivas']} positivas.\n\n",
        _tabla([_fila_modelo(n, m) for n, m in c["resultados"].items()], COLUMNAS_MODELO),
        "\n### Por equipo\n\n",
        _tabla([{"serie": k, **v} for k, v in c["por_serie"].items()],
               ["serie", "observaciones", "positivas", "average_precision", "episodios_evaluables",
                "episodios_anticipados", "falsas_alarmas_por_dia_sensor"]),
        "\n### Desplazamiento de distribución (KS)\n\n",
        _tabla([{"feature": k, **v} for k, v in c["desplazamiento_distribucion"].items()],
               ["feature", "hohenheim_media", "hospital_media", "hohenheim_std", "hospital_std", "ks"]),
    ]


def generar_markdown_resultados(rutas, resumen: dict) -> None:
    partes = [
        "# Resultados de experimentos (generado)\n",
        f"> Generado por el pipeline el {resumen['generado_en']}. Versiones: `{resumen['versiones']}`.\n",
        f"\n**Estado del modelo principal (A1): {resumen['estado_modelo_principal']}** — {resumen['criterios']['motivo']}\n",
        "\n## Criterios de aceptación (fijados antes de evaluar)\n\n",
        _tabla([{"criterio": k, **v} for k, v in resumen["criterios"]["evaluacion"].items()], COLUMNAS_CRITERIOS),
        f"\n**Variante con puerta (A2): {resumen['estado_variante_puerta']}** — "
        f"{resumen['criterios_variante_puerta']['motivo']}\n\n",
        _tabla([{"criterio": k, **v} for k, v in resumen["criterios_variante_puerta"]["evaluacion"].items()], COLUMNAS_CRITERIOS),
    ]
    for clave in ("A1", "A2", "A3"):
        r = resumen["experimentos"][clave]
        part = r["particion"]["auditoria_final"]["particiones"]
        partes += [
            f"\n## Experimento {clave}: {r['nombre']}\n",
            f"\n- Features ({len(r['features'])}): `{', '.join(r['features'])}`\n"
            f"- Observaciones excluidas: `{r['observaciones_excluidas']}`\n"
            f"- Desarrollo: {part['desarrollo']['observaciones']} obs, {part['desarrollo']['positivas']} positivas, "
            f"{part['desarrollo']['incidentes_objetivo']} incidentes; series {part['desarrollo']['series']}\n"
            f"- Prueba: {part['prueba']['observaciones']} obs, {part['prueba']['positivas']} positivas, "
            f"{part['prueba']['incidentes_objetivo']} incidentes; series {part['prueba']['series']}\n"
            f"- Auditoría de fugas: `{r['particion']['auditoria_final']['comprobaciones']}`\n"
            f"- Mejores hiperparámetros (AP fuera de pliegue): `{r['mejores_hiperparametros']}`\n"
            f"- Umbral RF (validación): `{r['umbral_random_forest']}`\n",
            "\n### Prueba independiente\n\n",
            _tabla([_fila_modelo(n, m) for n, m in r["prueba"].items()], COLUMNAS_MODELO),
            "\n### Validación hacia adelante (optimista para el umbral)\n\n",
            _tabla(
                [{"modelo": n, "AP": m["average_precision"], "recall_riesgo": m["por_clase"]["riesgo_preventivo"]["recall"],
                  "precision_riesgo": m["por_clase"]["riesgo_preventivo"]["precision"]} for n, m in r["validacion"].items()],
                ["modelo", "AP", "precision_riesgo", "recall_riesgo"],
            ),
        ]
        if clave == "A1":
            partes += [
                "\n### Estabilidad por sensor (prueba)\n\n",
                _tabla([{"serie": k, **v} for k, v in r["analisis_errores"]["por_sensor"].items()],
                       ["serie", "observaciones", "positivas", "average_precision", "episodios_evaluables",
                        "episodios_anticipados", "falsas_alarmas_por_dia_sensor"]),
                "\n### Estabilidad por día (prueba)\n\n",
                _tabla([{"dia": k, **v} for k, v in r["analisis_errores"]["por_dia"].items()],
                       ["dia", "observaciones", "positivas", "average_precision", "episodios_evaluables",
                        "episodios_anticipados", "falsas_alarmas_por_dia_sensor"]),
                f"\n- Subconjunto lejos del límite (T ≤ 7 °C): `{r['analisis_errores']['subconjunto_temp_le_7c']}`\n"
                f"- Spearman RF vs proximidad: {_fmt(r['analisis_errores']['spearman_rf_vs_proximidad'])}\n"
                f"- IC AP por bloques: `{r['incertidumbre_prueba']}`\n",
                "\n![PR](artefactos/fig_pr_prueba.png)\n\n![Importancias](artefactos/fig_importancias.png)\n\n"
                "![Anticipación](artefactos/fig_anticipacion.png)\n\n![Ejemplo](artefactos/fig_ejemplo_prueba.png)\n\n"
                "![Calibración](artefactos/fig_calibracion.png)\n",
            ]
    partes += ["\n## Experimento B (hospital)\n\n", f"`{resumen['experimentos']['B']}`\n"]
    partes += _seccion_transferencia("Experimento C (transferencia, A1 térmico → hospital)", resumen["experimentos"]["C"])
    partes.append("\n![Transferencia](artefactos/fig_transferencia.png)\n")
    partes += _seccion_transferencia("Experimento C2 (transferencia, A2 con puerta → hospital)", resumen["experimentos"]["C2"])
    partes += ["\n## Experimento D (ColdCare)\n\n", f"`{resumen['experimentos']['D']}`\n"]
    (rutas.docs / "RESULTADOS_EXPERIMENTOS.md").write_text("".join(partes), encoding="utf-8")
