"""Orquestación reproducible: auditoría de datos, experimentos A-D y registro.

Decisiones fijadas ANTES de entrenar (no se ajustan mirando la prueba):
- Horizonte principal: 30 min. 15 min es análisis de sensibilidad.
- Cortes temporales (hora local del recinto): validación hacia adelante con
  cortes 05-09, 06-09 y 07-09; prueba desde 09-09 00:00. La prueba incluye
  los sensores 05 y 09, que no existen en el periodo de desarrollo.
- Selección de hiperparámetros por Average Precision fuera de pliegue;
  umbral por F1 máximo en validación; criterios de aceptación en
  `CRITERIOS_ACEPTACION`.
"""

import io
import json
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import scipy
import sklearn
from scipy.stats import ks_2samp, spearmanr
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score

from src.infrastructure.ai.prediccion_preventiva import calidad, eventos, ingesta, registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    ESTADO_EXPERIMENTAL,
    ESTADO_RECHAZADO,
    ESTADO_VALIDADO_OFFLINE,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.entrenamiento import (
    SEMILLA,
    Observaciones,
    ajustar_baselines,
    baseline_mayoritario,
    baseline_prior,
    buscar_hiperparametros,
    construir_observaciones,
    crear_modelo,
    marca_tiempo,
    predicciones_fuera_de_pliegue,
    puntuacion_proximidad,
    puntuacion_regla_legada,
    puntuacion_tendencia,
)
from src.infrastructure.ai.prediccion_preventiva.etiquetas import etiquetar
from src.infrastructure.ai.prediccion_preventiva.evaluacion import (
    bootstrap_por_bloques,
    curva_pr,
    elegir_umbral_f1,
    metricas_evento,
    metricas_observacion,
)
from src.infrastructure.ai.prediccion_preventiva.features import CONJUNTO_TERMICO, CONJUNTO_TERMICO_PUERTA
from src.infrastructure.ai.prediccion_preventiva.particion import (
    auditar_particion,
    dividir_desarrollo_prueba,
    pliegues_walk_forward,
)

RAIZ_BACKEND = Path(__file__).resolve().parents[4]

ARCHIVOS_ESPERADOS = (
    "Raw.zip",
    "Preprocessed.zip",
    "overview.csv",
    "experiment_actions.csv",
    "DataPreprocessing.ipynb",
    "bio_data_10mindata_4d.csv",
)

FUENTES = {
    "hohenheim": {
        "titulo": "Temperature and Humidity Time Series of Cold Storage Room Monitoring",
        "autores": "Henrichs, E.; Stoll, F.; Krupitzer, C. (University of Hohenheim)",
        "doi": "10.5281/zenodo.15130001",
        "licencia": "Creative Commons Attribution 4.0 International (CC BY 4.0)",
        "publicado": "2025-04-03 (v1)",
        "articulo_relacionado": "Henrichs, Stoll, Krupitzer. Towards Self-Adaptive Monitoring of Storage "
        "Environments with Distributed Sensor Systems. IEEE ACSOS-C 2025, pp. 85-90. "
        "DOI 10.1109/ACSOS-C66519.2025.00033",
        "sensor": "DHT22 (9 unidades) en registradores Arduino, muestreo 5 s",
    },
    "hospital": {
        "titulo": "Multi-Node IoT Dataset for Real-Time Hospital Cold-Chain Monitoring",
        "autores": "Lon Kan Perez, I. B. K. (Universidad Autónoma del Perú)",
        "doi": "10.5281/zenodo.22101912",
        "licencia": "Creative Commons Attribution 4.0 International (CC BY 4.0)",
        "publicado": "2026-08-25 (v1)",
        "sensor": "dsTemperature (por su nombre, DS18B20; no documentado en el registro)",
    },
}

# DataPreprocessing.ipynb, celda «Filtering Datasets»: los autores declaran
# móviles a SENSOR01 y SENSOR02. El registro de movimientos que menciona la
# descripción de Zenodo no está en el experiment_actions.csv recibido.
SENSORES_MOVILES_HOHENHEIM = ("SENSOR01", "SENSOR02")
CORTES_WALK_FORWARD_LOCAL = ("2024-09-05", "2024-09-06", "2024-09-07")
CORTE_PRUEBA_LOCAL = "2024-09-09"
# Para agrupar por día local (bloques de remuestreo). Hospital: el registro
# no declara zona; se asume Lima (UTC-5) por la afiliación del autor.
DESFASE_LOCAL_H = {"hohenheim": 2, "hospital": -5, "coldcare": -5}

CRITERIOS_ACEPTACION = {
    # Suficiencia: con 10 incidentes todos anticipados, el límite inferior
    # Clopper-Pearson al 95 % es 0,69; con 5 sería 0,48 (no concluyente).
    "min_incidentes_independientes_prueba": 10,
    "min_sensibilidad_incidente": 0.80,
    "min_limite_inferior_ic95_sensibilidad_incidente": 0.50,
    "max_falsas_alarmas_por_dia_sensor": 2.0,
    "min_mediana_anticipacion_min": 5.0,
    "rf_supera_mejor_baseline_en_ap": True,
}


@dataclass(slots=True)
class Rutas:
    entradas: Path = RAIZ_BACKEND / "data" / "ml" / "external_inputs"
    trabajo: Path = RAIZ_BACKEND / "data" / "ml" / "work"
    docs: Path = RAIZ_BACKEND / "docs" / "ml"
    registro: Path = registro.RAIZ_REGISTRO

    @property
    def artefactos(self) -> Path:
        return self.docs / "artefactos"


@dataclass(slots=True)
class DatosFuente:
    nombre: str
    lecturas_crudas: pd.DataFrame
    lecturas: pd.DataFrame
    rejillas: pd.DataFrame
    episodios: pd.DataFrame
    etiquetadas: pd.DataFrame
    auditoria: dict = field(default_factory=dict)
    acciones: pd.DataFrame | None = None


def _local(fecha: str) -> pd.Timestamp:
    return pd.Timestamp(f"{fecha} 00:00:00").tz_localize(ingesta.ZONA_HOHENHEIM).tz_convert("UTC")


def verificar_entradas(rutas: Rutas) -> list[dict]:
    faltantes = [a for a in ARCHIVOS_ESPERADOS if not (rutas.entradas / a).exists()]
    if faltantes:
        raise FileNotFoundError(
            f"Faltan archivos en {rutas.entradas}: {faltantes}. Copie los archivos originales de Zenodo "
            "a esa carpeta (no se descargan sustitutos automáticamente)."
        )
    return [
        {
            "archivo": a,
            "bytes": (rutas.entradas / a).stat().st_size,
            "sha256": registro.sha256_archivo(rutas.entradas / a),
            "fuente": "hospital" if a.startswith("bio_data") else "hohenheim",
        }
        for a in ARCHIVOS_ESPERADOS
    ]


def revisar_notebook(ruta: Path) -> dict:
    """Revisión estática (sin ejecutar) del notebook de los autores."""
    nb = json.loads(ruta.read_text(encoding="utf-8"))
    codigo = "\n".join("".join(c.get("source", "")) for c in nb.get("cells", []) if c.get("cell_type") == "code")
    importaciones = sorted(set(re.findall(r"^\s*(?:import|from)\s+([\w\.]+)", codigo, flags=re.MULTILINE)))
    return {
        "ejecutado": False,
        "celdas_codigo": sum(1 for c in nb.get("cells", []) if c.get("cell_type") == "code"),
        "importaciones": importaciones,
        "escribe_archivos": bool(re.search(r"to_csv|open\([^)]*'w'", codigo)),
        "sensores_moviles_declarados": re.findall(r"sensors_movable\s*=\s*(\[[^\]]*\])", codigo),
        "observacion": "Elimina cabeceras repetidas, recorta al periodo común y rellena con NaN a 5 s los "
        "sensores incompletos. No accede a red ni ejecuta comandos del sistema.",
    }


def _perfiles(lecturas: pd.DataFrame, config: ConfiguracionPreventiva) -> dict:
    return {str(s): calidad.perfilar_serie(df, config) for s, df in lecturas.groupby("serie_id", sort=True)}


def _resumen_eventos(rejillas: pd.DataFrame, episodios: pd.DataFrame, etiquetadas: pd.DataFrame) -> dict:
    incidentes = eventos.resumen_incidentes(episodios)
    por_serie = {}
    for serie, eps in episodios.groupby("serie_id"):
        por_serie[str(serie)] = {
            "episodios": len(eps),
            "inicio_no_observado": int((~eps["inicio_observado"]).sum()),
            "altas": int((eps["tipo"] == "alta").sum()),
            "bajas": int((eps["tipo"] == "baja").sum()),
            "duracion_mediana_min": float(eps["duracion_min"].median()),
            "duracion_max_min": float(eps["duracion_min"].max()),
        }
    cercanos = rejillas["temperatura"].between(7.5, 8.0) | rejillas["temperatura"].between(2.0, 2.5)
    return {
        "puntos_rejilla": len(rejillas),
        "segmentos": int(rejillas["segmento_id"].nunique()),
        "puntos_cercanos_a_limite_0_5c": int(cercanos.sum()),
        "episodios_total": len(episodios),
        "episodios_inicio_no_observado": int((~episodios["inicio_observado"]).sum()) if len(episodios) else 0,
        "episodios_altos": int((episodios["tipo"] == "alta").sum()) if len(episodios) else 0,
        "episodios_bajos": int((episodios["tipo"] == "baja").sum()) if len(episodios) else 0,
        "incidentes_independientes": int(incidentes["inicio_observado"].sum()) if len(incidentes) else 0,
        "incidentes_solo_arranque_en_caliente": int((~incidentes["inicio_observado"]).sum()) if len(incidentes) else 0,
        "por_serie": por_serie,
        "estados_etiqueta": etiquetadas["estado_etiqueta"].value_counts().to_dict(),
    }


def preparar_hohenheim(rutas: Rutas, config: ConfiguracionPreventiva) -> DatosFuente:
    crudas, informe, acciones = ingesta.importar_hohenheim(
        rutas.entradas / "Raw.zip", rutas.entradas / "experiment_actions.csv"
    )
    inicio = acciones.loc[acciones["accion"] == "Begin", "inicio"].min()
    fin = acciones.loc[acciones["accion"] == "End", "inicio"].max()
    perfiles = _perfiles(crudas, config)
    recortadas = calidad.recortar_periodo(crudas, inicio, fin)
    perfiles_periodo = _perfiles(recortadas, config)

    decisiones = {}
    for serie, perfil in perfiles_periodo.items():
        if serie in SENSORES_MOVILES_HOHENHEIM:
            decisiones[serie] = {
                "incluido": False,
                "motivo": "sensor_movil_sin_registro_de_movimientos",
                "detalle": "Declarado móvil por los autores; alcanza "
                f"{perfil['temp_max']} °C (fuera de la cámara) y el CSV de acciones no registra sus movimientos.",
            }
        elif not perfil["pct_en_rango"]:
            decisiones[serie] = {
                "incluido": False,
                "motivo": "sin_lecturas_en_rango",
                "detalle": f"Mínimo {perfil['temp_min']} °C: nunca dentro de 2-8 °C, no aporta instantes "
                "preventivos y su episodio permanente fundiría todos los incidentes del recinto.",
            }
        else:
            decisiones[serie] = {"incluido": True, "motivo": "incluido", "detalle": ""}
    incluidas = [s for s, d in decisiones.items() if d["incluido"]]
    lecturas, informe_limpieza = calidad.limpiar(recortadas[recortadas["serie_id"].isin(incluidas)])

    rejillas = calidad.preparar_rejillas(lecturas, config)
    episodios = eventos.agrupar_incidentes(eventos.detectar_episodios(rejillas, config), config)
    etiquetadas = etiquetar(rejillas, episodios, config)

    preprocesado = {}
    for nombre, datos in sorted(ingesta.leer_zip_seguro(rutas.entradas / "Preprocessed.zip").items()):
        df = pd.read_csv(io.BytesIO(datos), sep=";")
        preprocesado[nombre] = {"filas": len(df), "nan_temperatura": int(df["Temperature"].isna().sum())}
    overview = pd.read_csv(rutas.entradas / "overview.csv", sep=";", index_col=0)

    auditoria = {
        "procedencia": FUENTES["hohenheim"],
        "importacion": informe.como_dict(),
        "esquema_original": "CSV ';' sin zona horaria: Date (dd.mm.yyyy); Time (HH:MM:SS); Temperature (C); "
        "Humidity (%). Cabecera repetida en cada reinicio del registrador.",
        "zona_horaria": "Supuesta CEST (UTC+2) para datos y acciones; convertida a UTC.",
        "periodo_experimento_utc": [inicio.isoformat(), fin.isoformat()],
        "perfil_crudo_por_sensor": perfiles,
        "perfil_periodo_por_sensor": perfiles_periodo,
        "decisiones_inclusion": decisiones,
        "limpieza": informe_limpieza.como_dict(),
        "acciones": acciones["accion"].value_counts().to_dict(),
        "aperturas_puerta": int((acciones["accion"] == "Door opened").sum()),
        "duracion_aperturas_min": (
            (acciones.loc[acciones["accion"] == "Door opened", "fin"] - acciones.loc[acciones["accion"] == "Door opened", "inicio"])
            .dt.total_seconds()
            .div(60)
            .describe()
            .round(2)
            .to_dict()
        ),
        "preprocessed_zip": {
            "usado": False,
            "motivo": "Derivado de Raw.zip con relleno NaN a 5 s para alinear sensores; se trabaja sobre los "
            "datos originales y se tratan los huecos explícitamente.",
            "archivos": preprocesado,
        },
        "overview_csv": {
            "usado": False,
            "comparacion_inicio_fin": {
                s: {"overview_inicio": str(overview.loc["Start", s]), "overview_fin": str(overview.loc["End", s])}
                for s in overview.columns
            },
        },
        "notebook": revisar_notebook(rutas.entradas / "DataPreprocessing.ipynb"),
        "eventos": _resumen_eventos(rejillas, episodios, etiquetadas),
    }
    return DatosFuente("hohenheim", crudas, lecturas, rejillas, episodios, etiquetadas, auditoria, acciones)


def preparar_hospital(rutas: Rutas, config: ConfiguracionPreventiva) -> DatosFuente:
    crudas, informe, tabla = ingesta.importar_hospital(rutas.entradas / "bio_data_10mindata_4d.csv")
    perfiles = _perfiles(crudas, config)
    decisiones = {}
    for serie, perfil in perfiles.items():
        if perfil["clase_equipo"] != calidad.EQUIPO_REFRIGERACION:
            decisiones[serie] = {
                "incluido": False,
                "motivo": perfil["clase_equipo"],
                "detalle": f"Mediana {perfil['temp_mediana']} °C: equipo no comparable con un refrigerador de "
                "2-8 °C; no se transforma para parecerlo.",
            }
        else:
            decisiones[serie] = {"incluido": True, "motivo": "incluido", "detalle": ""}
    incluidas = [s for s, d in decisiones.items() if d["incluido"]]
    lecturas, informe_limpieza = calidad.limpiar(crudas[crudas["serie_id"].isin(incluidas)])
    rejillas = calidad.preparar_rejillas(lecturas, config)
    episodios = eventos.agrupar_incidentes(eventos.detectar_episodios(rejillas, config), config)
    etiquetadas = etiquetar(rejillas, episodios, config)

    # Columnas sin nombre: ¿desplazan valores? Se compara la distribución de
    # dsTemperature entre filas de 9 y de 12 campos de cada equipo.
    comparacion = {}
    for serie, g in tabla.groupby("username"):
        comparacion[str(serie)] = {
            str(c): {
                "filas": len(gc),
                "dsTemperature_mediana": None if gc["temperatura_interna"].dropna().empty else float(gc["temperatura_interna"].median()),
                "temp_aux_mediana": None if gc["temp_aux"].dropna().empty else float(gc["temp_aux"].median()),
                "humidity_mediana": None if gc["humedad"].dropna().empty else float(gc["humedad"].median()),
            }
            for c, gc in g.groupby("campos")
        }
    puertas = tabla[tabla["doorStatus"].str.lower() == "open"]
    auditoria = {
        "procedencia": FUENTES["hospital"],
        "importacion": informe.como_dict(),
        "esquema_original": "CSV ',' con cabecera de 9 nombres (_id, __v, createdAt, datetime, doorStatus, "
        "dsTemperature, humidity, time_slot, username); 13 279 filas traen 3 campos más sin nombre.",
        "columnas_sin_nombre": {
            "interpretacion": "ref_dispositivo (ObjectId), temperatura auxiliar ~20-25 °C (probable sensor "
            "ambiental) y updatedAt. Semántica INFERIDA, no documentada: no se usan como variables.",
            "comparacion_9_vs_12_campos": comparacion,
            "desplazamiento_detectado": False,
        },
        "zona_horaria": "Timestamps ISO 8601 con 'Z' (UTC).",
        "perfil_por_equipo": perfiles,
        "decisiones_inclusion": decisiones,
        "limpieza": informe_limpieza.como_dict(),
        "aperturas_puerta_registradas": {str(k): int(v) for k, v in puertas["username"].value_counts().items()},
        "eventos": _resumen_eventos(rejillas, episodios, etiquetadas),
    }
    return DatosFuente("hospital", crudas, lecturas, rejillas, episodios, etiquetadas, auditoria, None)


def _con_bloques(obs: Observaciones) -> Observaciones:
    desfase = obs.tabla["fuente"].map(DESFASE_LOCAL_H).fillna(0).astype(int)
    local = obs.tabla["timestamp"] + pd.to_timedelta(desfase, unit="h")
    obs.tabla["dia_local"] = local.dt.strftime("%Y-%m-%d")
    obs.tabla["bloque"] = obs.tabla["entorno_id"] + "|" + obs.tabla["dia_local"]
    return obs


def _evaluar_puntuaciones(
    nombre: str, obs: Observaciones, idx: np.ndarray, puntuacion: np.ndarray, umbral: float | None,
    episodios: pd.DataFrame, config: ConfiguracionPreventiva, es_probabilidad: bool,
) -> dict:
    y = obs.y[idx]
    tabla = obs.tabla.iloc[idx].reset_index(drop=True)
    if umbral is None:
        return {"modelo": nombre, "estado": "no estimable", "motivo": "sin umbral de validación"}
    alarma = puntuacion >= umbral
    return {
        "modelo": nombre,
        "observacion": metricas_observacion(y, puntuacion, umbral, es_probabilidad),
        "evento": metricas_evento(tabla, alarma, episodios, config),
    }


def _por_grupo(obs: Observaciones, idx: np.ndarray, puntuacion: np.ndarray, umbral: float, episodios, config, columna: str) -> dict:
    tabla = obs.tabla.iloc[idx].reset_index(drop=True)
    salida = {}
    for valor, sub in tabla.groupby(columna):
        pos = sub.index.to_numpy()
        y = obs.y[idx][pos]
        ev = metricas_evento(sub.reset_index(drop=True), puntuacion[pos] >= umbral, episodios[episodios["serie_id"].isin(sub["serie_id"].unique())], config)
        salida[str(valor)] = {
            "observaciones": int(pos.size),
            "positivas": int(y.sum()),
            "average_precision": float(average_precision_score(y, puntuacion[pos])) if 0 < y.sum() < y.size else None,
            "episodios_evaluables": ev["episodios_evaluables"],
            "episodios_anticipados": ev["episodios_anticipados"],
            "falsas_alarmas_por_dia_sensor": ev["falsas_alarmas_por_dia_sensor"],
        }
    return salida


def ejecutar_experimento_hohenheim(
    datos: DatosFuente, config: ConfiguracionPreventiva, conjunto: str, nombre: str, semilla: int = SEMILLA
) -> dict:
    obs = _con_bloques(construir_observaciones(datos.etiquetadas, config, conjunto))
    corte_prueba = _local(CORTE_PRUEBA_LOCAL)
    cortes = [_local(c) for c in CORTES_WALK_FORWARD_LOCAL]
    dev, prueba = dividir_desarrollo_prueba(obs.tabla, corte_prueba, config)
    pliegues = pliegues_walk_forward(obs.tabla, cortes, corte_prueba, config, list(CORTES_WALK_FORWARD_LOCAL))

    auditoria_final = auditar_particion(obs.tabla, {"desarrollo": dev, "prueba": prueba}, config)
    auditorias_pliegues = {
        p.nombre: auditar_particion(obs.tabla, {"entrenamiento": p.entrenamiento, "validacion": p.validacion}, config)
        for p in pliegues
    }
    if not auditoria_final["aprobada"] or not all(a["aprobada"] for a in auditorias_pliegues.values()):
        raise RuntimeError(f"{nombre}: la auditoría de partición detectó fugas; se detiene el entrenamiento")

    x, y = obs.x, obs.y
    mejores, busqueda = buscar_hiperparametros(x, y, pliegues, semilla)
    oof, por_pliegue = predicciones_fuera_de_pliegue(x, y, pliegues, mejores, semilla)
    va = np.flatnonzero(np.isfinite(oof))
    umbral_rf = elegir_umbral_f1(y[va], oof[va])
    baselines = ajustar_baselines(obs, datos.etiquetadas, va, config)
    puntuaciones_base = baselines.pop("_puntuaciones")

    modelo = crear_modelo(mejores, semilla).fit(x[dev], y[dev])
    # Inferencia con un solo hilo: en paralelo sklearn suma las probabilidades
    # de los árboles en orden variable y el último decimal cambia entre
    # ejecuciones. Así el artefacto produce puntuaciones bit a bit idénticas
    # (y en producción se predice de a una fila, donde el paralelismo no aporta).
    modelo.set_params(n_jobs=1)
    p_prueba = modelo.predict_proba(x[prueba])[:, 1]
    episodios_prueba = datos.episodios[datos.episodios["inicio_ns"] >= int(corte_prueba.value)]

    resultados_prueba = {
        "random_forest": _evaluar_puntuaciones(
            "random_forest", obs, prueba, p_prueba, umbral_rf["umbral"], episodios_prueba, config, True
        ),
        "clase_mayoritaria": _evaluar_puntuaciones(
            "clase_mayoritaria", obs, prueba, baseline_mayoritario(y[dev], prueba.size), 0.5, episodios_prueba, config, True
        ),
        "clase_prior": _evaluar_puntuaciones(
            "clase_prior", obs, prueba, baseline_prior(y[dev], prueba.size), 0.5, episodios_prueba, config, True
        ),
    }
    for nombre_base, puntuacion in puntuaciones_base.items():
        resultados_prueba[nombre_base] = _evaluar_puntuaciones(
            nombre_base, obs, prueba, puntuacion[prueba], baselines[nombre_base]["umbral"]["umbral"],
            episodios_prueba, config, False,
        )

    # Validación (fuera de pliegue): el umbral se eligió aquí, así que estas
    # cifras son optimistas para el umbral; se reportan como validación.
    resultados_validacion = {
        "random_forest": metricas_observacion(y[va], oof[va], umbral_rf["umbral"], True),
        **{
            n: metricas_observacion(y[va], s[va], baselines[n]["umbral"]["umbral"], False)
            for n, s in puntuaciones_base.items()
        },
    }

    # Incertidumbre por bloques (entorno x día) en prueba.
    tabla_prueba = obs.tabla.iloc[prueba].reset_index(drop=True).assign(_p=p_prueba, _y=y[prueba])
    ic_ap = bootstrap_por_bloques(
        tabla_prueba,
        lambda d: average_precision_score(d["_y"], d["_p"]) if 0 < d["_y"].sum() < len(d) else None,
        semilla,
    )

    # Interpretabilidad: importancia por impureza (entrenamiento) y por
    # permutación sobre la prueba, que el modelo no vio.
    perm = permutation_importance(
        modelo, x[prueba], y[prueba], scoring="average_precision", n_repeats=10, random_state=semilla, n_jobs=-1
    )
    importancias = {
        "impureza": dict(zip(obs.nombres, modelo.feature_importances_.round(5).tolist(), strict=True)),
        "permutacion_prueba_media": dict(zip(obs.nombres, perm.importances_mean.round(5).tolist(), strict=True)),
        "permutacion_prueba_std": dict(zip(obs.nombres, perm.importances_std.round(5).tolist(), strict=True)),
    }

    proximidad_prueba = puntuacion_proximidad(obs)[prueba]
    lejos = obs.tabla.iloc[prueba]["temperatura"].to_numpy() <= 7.0
    y_lejos = y[prueba][lejos]
    analisis = {
        "spearman_rf_vs_proximidad": float(spearmanr(p_prueba, proximidad_prueba).statistic),
        "subconjunto_temp_le_7c": {
            "observaciones": int(lejos.sum()),
            "positivas": int(y_lejos.sum()),
            "ap_random_forest": float(average_precision_score(y_lejos, p_prueba[lejos])) if 0 < y_lejos.sum() < y_lejos.size else None,
            "ap_proximidad": float(average_precision_score(y_lejos, proximidad_prueba[lejos])) if 0 < y_lejos.sum() < y_lejos.size else None,
            "prevalencia": float(y_lejos.mean()) if y_lejos.size else None,
        },
        "por_sensor": _por_grupo(obs, prueba, p_prueba, umbral_rf["umbral"], episodios_prueba, config, "serie_id"),
        "por_dia": _por_grupo(obs, prueba, p_prueba, umbral_rf["umbral"], episodios_prueba, config, "dia_local"),
    }
    errores = tabla_prueba.assign(alarma=p_prueba >= umbral_rf["umbral"])
    analisis["falsos_negativos_minutos_a_excursion"] = (
        errores.loc[(errores["_y"] == 1) & ~errores["alarma"], "minutos_a_excursion"].describe().round(2).to_dict()
    )
    analisis["falsos_positivos_temp_actual"] = (
        errores.loc[(errores["_y"] == 0) & errores["alarma"], "temperatura"].describe().round(3).to_dict()
    )

    return {
        "nombre": nombre,
        "conjunto_features": conjunto,
        "features": obs.nombres,
        "configuracion": config.como_dict(),
        "observaciones_excluidas": obs.excluidas,
        "particion": {
            "corte_prueba_utc": corte_prueba.isoformat(),
            "cortes_walk_forward_utc": [c.isoformat() for c in cortes],
            "auditoria_final": auditoria_final,
            "auditoria_pliegues": auditorias_pliegues,
        },
        "busqueda_hiperparametros": busqueda,
        "mejores_hiperparametros": mejores,
        "pliegues_mejor_modelo": por_pliegue,
        "umbral_random_forest": umbral_rf,
        "baselines_ajustados": baselines,
        "validacion": resultados_validacion,
        "prueba": resultados_prueba,
        "incertidumbre_prueba": {"average_precision_rf_bootstrap_bloques": ic_ap},
        "importancias": importancias,
        "analisis_errores": analisis,
        "curvas_pr_prueba": {
            "random_forest": curva_pr(y[prueba], p_prueba),
            **{n: curva_pr(y[prueba], s[prueba]) for n, s in puntuaciones_base.items()},
        },
        "_modelo": modelo,
        "_obs": obs,
        "_indices": {"desarrollo": dev, "prueba": prueba, "validacion": va},
        "_puntuaciones": {"prueba_rf": p_prueba, "oof_rf": oof, **{f"base_{n}": s for n, s in puntuaciones_base.items()}},
    }


def ejecutar_transferencia(resultado_a: dict, hospital: DatosFuente, config: ConfiguracionPreventiva) -> dict:
    """Experimento C: modelo A entrenado en Hohenheim, evaluado sin
    reentrenar sobre los refrigeradores compatibles del hospital."""
    obs = _con_bloques(construir_observaciones(hospital.etiquetadas, config, resultado_a["conjunto_features"]))
    if len(obs.tabla) == 0:
        return {"viable": False, "motivo": "sin observaciones elegibles en el hospital"}
    todos = np.arange(len(obs.tabla))
    modelo = resultado_a["_modelo"]
    p = modelo.predict_proba(obs.x)[:, 1]
    base = resultado_a["baselines_ajustados"]
    puntuaciones = {
        "proximidad_limite": puntuacion_proximidad(obs),
        "tendencia_reciente": puntuacion_tendencia(obs, base["tendencia_reciente"]["horizonte_extrapolacion_min"], config),
        "regla_legada": puntuacion_regla_legada(hospital.etiquetadas, obs, config),
    }
    resultados = {
        "random_forest": _evaluar_puntuaciones(
            "random_forest", obs, todos, p, resultado_a["umbral_random_forest"]["umbral"], hospital.episodios, config, True
        )
    }
    for n, s in puntuaciones.items():
        resultados[n] = _evaluar_puntuaciones(n, obs, todos, s, base[n]["umbral"]["umbral"], hospital.episodios, config, False)

    origen = resultado_a["_obs"]
    dev = resultado_a["_indices"]["desarrollo"]
    desplazamiento = {}
    for j, nombre in enumerate(obs.nombres):
        a, b = origen.x[dev, j], obs.x[:, j]
        desplazamiento[nombre] = {
            "hohenheim_media": float(a.mean()), "hohenheim_std": float(a.std()),
            "hospital_media": float(b.mean()), "hospital_std": float(b.std()),
            "ks": float(ks_2samp(a, b).statistic),
        }
    return {
        "viable": True,
        "series": sorted(obs.tabla["serie_id"].unique().tolist()),
        "observaciones": len(obs.tabla),
        "positivas": int(obs.y.sum()),
        "por_serie": _por_grupo(obs, todos, p, resultado_a["umbral_random_forest"]["umbral"], hospital.episodios, config, "serie_id"),
        "resultados": resultados,
        "desplazamiento_distribucion": desplazamiento,
        "_obs": obs,
        "_p": p,
    }


def viabilidad_hospital(hospital: DatosFuente) -> dict:
    ev = hospital.auditoria["eventos"]
    incidentes = ev["incidentes_independientes"]
    return {
        "experimento": "B",
        "viable_para_entrenar_y_evaluar": False,
        "incidentes_independientes": incidentes,
        "series_con_episodios": sorted(hospital.episodios["serie_id"].unique().tolist()),
        "motivo": f"{incidentes} episodios, todos en un único equipo y un único día: no permiten una partición "
        "temporal con eventos en entrenamiento y en prueba. Se usa solo como prueba externa (Experimento C).",
    }


def evaluar_criterios(resultado: dict) -> tuple[str, dict]:
    rf = resultado["prueba"]["random_forest"]
    ev = rf["evento"]
    c = CRITERIOS_ACEPTACION
    sens = ev["sensibilidad_por_incidente"]
    ap = lambda r: r["observacion"]["average_precision"] if isinstance(r["observacion"]["average_precision"], float) else -1.0  # noqa: E731
    mejor_base = max(
        (r for n, r in resultado["prueba"].items() if n != "random_forest" and "observacion" in r), key=ap
    )
    anticip = ev["anticipacion_min"]
    falsas = ev["falsas_alarmas_por_dia_sensor"]
    detalle = {
        "suficiencia_incidentes": {
            "valor": ev["incidentes_evaluables"], "umbral": c["min_incidentes_independientes_prueba"],
            "cumple": ev["incidentes_evaluables"] >= c["min_incidentes_independientes_prueba"],
        },
        "sensibilidad_incidente": {
            "valor": sens.get("valor"), "umbral": c["min_sensibilidad_incidente"],
            "cumple": sens.get("valor") is not None and sens["valor"] >= c["min_sensibilidad_incidente"],
        },
        "limite_inferior_ic95": {
            "valor": sens.get("ic95_exacto", [None])[0], "umbral": c["min_limite_inferior_ic95_sensibilidad_incidente"],
            "cumple": sens.get("ic95_exacto") is not None and sens["ic95_exacto"][0] >= c["min_limite_inferior_ic95_sensibilidad_incidente"],
        },
        "falsas_alarmas_por_dia_sensor": {
            "valor": falsas if isinstance(falsas, float) else None, "umbral": c["max_falsas_alarmas_por_dia_sensor"],
            "cumple": isinstance(falsas, float) and falsas <= c["max_falsas_alarmas_por_dia_sensor"],
        },
        "mediana_anticipacion_min": {
            "valor": anticip.get("mediana"), "umbral": c["min_mediana_anticipacion_min"],
            "cumple": anticip.get("mediana") is not None and anticip["mediana"] >= c["min_mediana_anticipacion_min"],
        },
        "rf_supera_mejor_baseline_ap": {
            "valor_rf": ap(rf), "mejor_baseline": mejor_base["modelo"], "valor_baseline": ap(mejor_base),
            "cumple": ap(rf) > ap(mejor_base),
        },
    }
    # Segundo juego de criterios (F1 macro ≥ 0,85, el que cita el TI): se
    # reporta siempre junto al principal, sin decidir el estado por sí solo.
    f1 = rf["observacion"].get("macro_f1")
    criterio_f1 = {"valor": f1, "umbral": 0.85, "cumple": f1 is not None and f1 >= 0.85}
    if not detalle["suficiencia_incidentes"]["cumple"]:
        estado = ESTADO_EXPERIMENTAL
        motivo = "Incidentes independientes en prueba insuficientes para una evaluación concluyente."
    elif all(v["cumple"] for v in detalle.values()):
        estado = ESTADO_VALIDADO_OFFLINE
        motivo = "Cumple todos los criterios offline; requiere Experimento D y aprobación humana para desplegar."
    else:
        estado = ESTADO_RECHAZADO
        motivo = "Con eventos suficientes, no alcanza los criterios de desempeño."
    return estado, {"criterios": c, "evaluacion": detalle, "criterio_f1": criterio_f1, "motivo": motivo}


def _sin_privados(d):
    if isinstance(d, dict):
        return {k: _sin_privados(v) for k, v in d.items() if not str(k).startswith("_")}
    if isinstance(d, list):
        return [_sin_privados(v) for v in d]
    if isinstance(d, (np.floating, np.integer)):
        return d.item()
    if isinstance(d, np.ndarray):
        return d.tolist()
    if isinstance(d, (pd.Timestamp,)):
        return d.isoformat()
    return d


def versiones_entorno() -> dict:
    return {
        "python": platform.python_version(),
        "scikit_learn": sklearn.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
        "joblib": joblib.__version__,
        "plataforma": platform.platform(),
    }


def construir_manifiesto(version: str, resultado: dict, estado: str, criterios: dict, entradas: list[dict]) -> dict:
    rf = resultado["prueba"]["random_forest"]
    return {
        "version": version,
        "algoritmo": "sklearn.ensemble.RandomForestClassifier",
        "tarea": "clasificación preventiva binaria normal(0) / riesgo_preventivo(1)",
        "clases": {"0": "normal", "1": "riesgo_preventivo"},
        "entrenado_en": marca_tiempo(),
        "estado": estado,
        "motivo_estado": criterios["motivo"],
        "fuente_entrenamiento": "hohenheim",
        "datasets": [e for e in entradas if e["fuente"] == "hohenheim"],
        "conjunto_features": resultado["conjunto_features"],
        "features": resultado["features"],
        "configuracion": resultado["configuracion"],
        "hiperparametros": resultado["_modelo"].get_params(),
        "semilla": SEMILLA,
        "umbral_decision": resultado["umbral_random_forest"]["umbral"],
        "origen_umbral": "F1 máximo en predicciones de validación hacia adelante (fuera de pliegue)",
        "puntuacion_calibrada": False,
        "versiones": versiones_entorno(),
        "metricas_prueba": _sin_privados(
            {
                "average_precision": rf["observacion"]["average_precision"],
                "recall_riesgo": rf["observacion"]["por_clase"]["riesgo_preventivo"]["recall"],
                "precision_riesgo": rf["observacion"]["por_clase"]["riesgo_preventivo"]["precision"],
                "sensibilidad_por_incidente": rf["evento"]["sensibilidad_por_incidente"],
                "falsas_alarmas_por_dia_sensor": rf["evento"]["falsas_alarmas_por_dia_sensor"],
                "anticipacion_min": rf["evento"]["anticipacion_min"],
            }
        ),
        "criterios_aceptacion": _sin_privados(criterios),
    }


def ejecutar_auditoria(rutas: Rutas | None = None) -> tuple[DatosFuente, DatosFuente, dict, list[dict]]:
    """Fase 2: ingesta, limpieza, eventos y etiquetas de ambas fuentes, con
    sus artefactos (JSON, CSV, figuras de series y DATASET_AUDIT.md)."""
    rutas = rutas or Rutas()
    rutas.trabajo.mkdir(parents=True, exist_ok=True)
    rutas.artefactos.mkdir(parents=True, exist_ok=True)
    entradas = verificar_entradas(rutas)
    config = ConfiguracionPreventiva()
    hohenheim = preparar_hohenheim(rutas, config)
    hospital = preparar_hospital(rutas, config)
    auditoria = {
        "generado_en": marca_tiempo(),
        "entradas": entradas,
        "configuracion": config.como_dict(),
        "hohenheim": _sin_privados(hohenheim.auditoria),
        "hospital": _sin_privados(hospital.auditoria),
    }
    (rutas.artefactos / "auditoria_datasets.json").write_text(
        json.dumps(auditoria, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    for nombre, eps in (("hohenheim", hohenheim.episodios), ("hospital", hospital.episodios)):
        eps.assign(
            inicio_utc=pd.to_datetime(eps["inicio_ns"], unit="ns", utc=True),
            fin_utc=pd.to_datetime(eps["fin_ns"], unit="ns", utc=True),
        ).drop(columns=["inicio_ns", "fin_ns"]).to_csv(rutas.artefactos / f"episodios_{nombre}.csv", index=False)
    eventos.resumen_incidentes(hohenheim.episodios).to_csv(rutas.artefactos / "incidentes_hohenheim.csv", index=False)

    from src.infrastructure.ai.prediccion_preventiva import reporte  # importación perezosa: matplotlib

    reporte.figura_series_hohenheim(rutas, hohenheim, config)
    reporte.figura_series_hospital(rutas, hospital, config)
    reporte.generar_markdown_auditoria(rutas, auditoria, hohenheim, hospital)
    return hohenheim, hospital, auditoria, entradas


def ejecutar_todo(rutas: Rutas | None = None, registrar_modelo: bool = True, version: str | None = None) -> dict:
    rutas = rutas or Rutas()
    hohenheim, hospital, _, entradas = ejecutar_auditoria(rutas)
    config = ConfiguracionPreventiva()

    a1 = ejecutar_experimento_hohenheim(hohenheim, config, CONJUNTO_TERMICO, "A1_hohenheim_termico_h30")
    a2 = ejecutar_experimento_hohenheim(hohenheim, config, CONJUNTO_TERMICO_PUERTA, "A2_hohenheim_termico_puerta_h30")
    config15 = ConfiguracionPreventiva(horizonte_min=15)
    hohenheim15 = DatosFuente(
        "hohenheim", hohenheim.lecturas_crudas, hohenheim.lecturas, hohenheim.rejillas, hohenheim.episodios,
        etiquetar(hohenheim.rejillas, hohenheim.episodios, config15), hohenheim.auditoria, hohenheim.acciones,
    )
    a3 = ejecutar_experimento_hohenheim(hohenheim15, config15, CONJUNTO_TERMICO, "A3_hohenheim_termico_h15")
    c = ejecutar_transferencia(a1, hospital, config)
    c2 = ejecutar_transferencia(a2, hospital, config)
    b = viabilidad_hospital(hospital)
    estado, criterios = evaluar_criterios(a1)
    estado_a2, criterios_a2 = evaluar_criterios(a2)

    resumen = {
        "generado_en": marca_tiempo(),
        "versiones": versiones_entorno(),
        "entradas": entradas,
        "configuracion": config.como_dict(),
        "estado_modelo_principal": estado,
        "criterios": criterios,
        "estado_variante_puerta": estado_a2,
        "criterios_variante_puerta": criterios_a2,
        "experimentos": {
            "A1": _sin_privados(a1),
            "A2": _sin_privados(a2),
            "A3": _sin_privados(a3),
            "B": b,
            "C": _sin_privados(c),
            "C2": _sin_privados(c2),
            "D": {
                "estado": "pendiente",
                "motivo": "No hay todavía mediciones del ESP32/DS18B20 de ColdCare. El importador "
                "(ingesta.importar_coldcare_csv) y el comando `evaluar-coldcare` están listos.",
            },
        },
    }
    (rutas.artefactos / "resultados_experimentos.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    pd.DataFrame(
        [{**r["parametros"], "ap_oof": r["ap_oof"]} for r in a1["busqueda_hiperparametros"]]
    ).to_csv(rutas.artefactos / "busqueda_hiperparametros_A1.csv", index=False)
    obs_prueba = a1["_obs"].tabla.iloc[a1["_indices"]["prueba"]]
    obs_prueba.assign(puntuacion_rf=a1["_puntuaciones"]["prueba_rf"])[
        ["serie_id", "timestamp", "temperatura", "y", "minutos_a_excursion", "incidente_objetivo", "puntuacion_rf"]
    ].to_csv(rutas.trabajo / "predicciones_prueba_A1.csv", index=False)

    from src.infrastructure.ai.prediccion_preventiva import reporte  # importación perezosa: matplotlib

    reporte.generar_figuras(rutas, hohenheim, hospital, a1, c, config)
    reporte.generar_markdown_resultados(rutas, resumen)

    if registrar_modelo:
        sufijo = version or "1.0.0"
        resumen["modelos_registrados"] = []
        for nombre, resultado, est, crit in (
            ("rf-preventivo-h30-termico", a1, estado, criterios),
            ("rf-preventivo-h30-termico-puerta", a2, estado_a2, criterios_a2),
        ):
            v = f"{nombre}-{sufijo}"
            destino = registro.registrar(resultado["_modelo"], construir_manifiesto(v, resultado, est, crit, entradas), rutas.registro)
            resumen["modelos_registrados"].append({"version": v, "ruta": destino.relative_to(RAIZ_BACKEND).as_posix(), "estado": est})
    return resumen
