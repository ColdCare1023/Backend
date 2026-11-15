"""P-2 (HU-18): registra el modelo entrenado con las corridas controladas.

    python -m src.infrastructure.ai.prediccion_preventiva corridas --csv corridas.csv --prueba C4,C5 \
        [--validacion C3] --registrar --version V [--conjunto termico_minimo]

Entrena y evalúa con el MISMO pipeline del informe (`corridas.py`) y registra
con `registro.registrar`, la única ruta que anota el SHA-256 del manifiesto en
`historial.json`. Escribe `models/preventivo/<V>/modelo.joblib`,
`models/preventivo/<V>/manifiesto.json` y una entrada `registrado` en
`models/preventivo/historial.json`, en estado EXPERIMENTAL. El manifiesto lleva
lo que comprueban la carga (`servicio._cargar_y_validar`) y POST /activar
(`servicio.motivos_activacion`): `configuracion` (con `horizonte_min`),
`conjunto_features`, `features`, `umbral_decision`, `versiones` (`scikit_learn`,
`python`, `numpy`...) y `artefacto.sha256`; además la procedencia de los datos
(SHA-256 del CSV exportado, corridas y rango de fechas) y qué corridas
ajustaron el modelo, cuáles eligieron el umbral y cuáles lo evaluaron (P-3).

Se niega, con un mensaje en español y sin escribir nada, si:

- el scikit-learn instalado no es el fijado en `requirements.txt` (P-6): el
  backend carga el artefacto con esa versión;
- la versión no es válida, supera los 50 caracteres del registro de la base o
  ya existe (el registro nunca sobrescribe), también si solo queda su entrada
  en `historial.json`;
- las corridas no alcanzan para ajustar, elegir el umbral fuera de muestra y
  evaluar con ambas clases (pocas corridas o etiquetas);
- el servicio de inferencia no puede servir el conjunto: cargado en sombra
  desde un registro temporal, no clasifica una lectura de prueba con el mismo
  vector que el entrenamiento (`comprobar_servicio`).

Solo el conjunto que se registra debe poder evaluarse: la ablación con el
SHT31 queda "no estimable" si las corridas no lo tienen. Sin separación
medible entre ajuste y prueba (otro equipo) registra con una advertencia:
POST /activar responderá 409 `faltan_particiones`.
"""

import math
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.prediccion_preventiva import calidad, registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_OK,
    ESTADO_EXPERIMENTAL,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.corridas import (
    DESCRIPCION_METODO_UMBRAL,
    ejecutar_pipeline_corridas,
    sin_privados,
)
from src.infrastructure.ai.prediccion_preventiva.entrenamiento import SEMILLA
from src.infrastructure.ai.prediccion_preventiva.features import (
    CONJUNTO_TERMICO,
    CONJUNTOS,
    features_de_rejillas,
    nombres_features,
)
from src.infrastructure.ai.prediccion_preventiva.ingesta import a_epoch_ns

RAIZ_BACKEND = Path(__file__).resolve().parents[4]
ARCHIVO_REQUISITOS = RAIZ_BACKEND / "requirements.txt"
PATRON_SKLEARN_FIJADO = re.compile(r"^scikit-learn==([0-9][0-9A-Za-z.]*)", re.MULTILINE)
# Límites de RegistrarVersionModeloRequest (POST /api/ia/modelo/versiones): una
# versión más larga se registraría aquí y no se podría registrar en la base.
LONGITUD_MAX_VERSION = 50
LONGITUD_MAX_DATASET = 50
FUENTE_CORRIDAS = "coldcare_corridas"

# Paridad con el servicio (`comprobar_servicio`). El servicio guarda el vector
# y la puntuación redondeados a 6 decimales (evidencia HU-47): la tolerancia es
# ese redondeo más el ruido de coma flotante (1e-9).
TOLERANCIA_PARIDAD = 0.5e-6 + 1e-9
# Historial que recibe el servicio en la comprobación: holgado frente a la
# ventana de 15 min más el hueco que corta un segmento.
HISTORIAL_PARIDAD = timedelta(hours=1)
# Lecturas de prueba que se revisan, de la más reciente hacia atrás, hasta dar
# con una cuya ventana el extractor del entrenamiento pueda calcular.
MAX_CANDIDATAS_PARIDAD = 200
_EPOCA = datetime(1970, 1, 1, tzinfo=timezone.utc)


class RegistroRechazado(RuntimeError):
    """No se registra el modelo. El mensaje es para quien ejecuta la CLI."""


# ── Comprobaciones previas (antes de entrenar) ──────────────────


def version_sklearn_fijada(requisitos: Path | None = None) -> str:
    requisitos = requisitos or ARCHIVO_REQUISITOS
    try:
        texto = requisitos.read_text(encoding="utf-8")
    except OSError:
        raise RegistroRechazado(
            f"no se pudo leer {requisitos.name} para comprobar la versión fijada de scikit-learn (P-6)"
        ) from None
    coincidencia = PATRON_SKLEARN_FIJADO.search(texto)
    if coincidencia is None:
        raise RegistroRechazado(f"{requisitos.name} no fija scikit-learn con '==' (P-6)")
    return coincidencia.group(1)


def exigir_sklearn_fijado(requisitos: Path | None = None) -> str:
    """P-6: el artefacto se entrena con la versión EXACTA que instala el
    backend. Otra versión menor no cargaría (`incompatible`) y otro parche
    cargaría sin garantía de predecir igual."""
    fijada = version_sklearn_fijada(requisitos)
    instalada = sklearn.__version__
    if instalada != fijada:
        raise RegistroRechazado(
            f"el scikit-learn instalado ({instalada}) no es el fijado en requirements.txt ({fijada}). "
            "El backend carga el modelo con la versión fijada: entrene en un entorno instalado con "
            "`pip install --require-hashes -r requirements-ml.txt` (P-6)"
        )
    return fijada


def exigir_version_nueva(version: str, raiz: Path) -> None:
    if len(version) > LONGITUD_MAX_VERSION:
        raise RegistroRechazado(
            f"la versión {version!r} tiene {len(version)} caracteres; el registro de la base admite "
            f"{LONGITUD_MAX_VERSION} como máximo"
        )
    try:
        destino = registro.directorio_version(version, raiz)
    except registro.ErrorRegistro as exc:
        raise RegistroRechazado(f"{exc} (minúsculas, dígitos, '.' y '-')") from None
    if destino.exists():
        raise RegistroRechazado(
            f"la versión {version} ya existe en el registro; el registro nunca sobrescribe una versión "
            "(use una versión nueva)"
        )
    try:
        anotada = registro.version_en_historial(version, raiz)
    except registro.ErrorRegistro as exc:
        raise RegistroRechazado(f"{exc}: no se puede comprobar que la versión {version} sea nueva") from None
    if anotada:
        raise RegistroRechazado(
            f"la versión {version} ya existe en el registro: figura en historial.json aunque su directorio "
            "no está; el registro nunca reutiliza una versión (use una versión nueva)"
        )


def _exigir_datos_suficientes(resultado: dict, conjunto: str) -> None:
    """Pocas corridas o etiquetas: el pipeline marca el conjunto como no
    estimable (sin ajuste con ambas clases, menos de MIN_CORRIDAS_AJUSTE
    corridas o sin umbral fuera de muestra). Además la prueba debe tener ambas
    clases: sin ella no hay métricas que registrar."""
    if resultado.get("resultado") != "evaluado":
        raise RegistroRechazado(
            f"con estas corridas el conjunto {conjunto} no se puede ajustar, elegir el umbral fuera de "
            f"muestra y evaluar ({resultado.get('resultado')})"
        )
    observaciones_prueba = resultado["particiones"]["observaciones_prueba"]
    positivas_prueba = resultado["positivas_prueba"]
    if positivas_prueba == 0 or positivas_prueba == observaciones_prueba:
        raise RegistroRechazado(
            f"las corridas de prueba {resultado['particiones']['prueba']} no tienen ambas clases "
            f"({positivas_prueba} positivas de {observaciones_prueba}): no hay métricas que registrar"
        )


def _exigir_compatible(modelo, nombres: list[str], config: ConfiguracionPreventiva, conjunto: str) -> None:
    """Las mismas condiciones que exige la carga en el servicio, comprobadas
    antes de escribir: si fallaran, el artefacto registrado no cargaría."""
    if nombres != nombres_features(config, conjunto) or getattr(modelo, "n_features_in_", None) != len(nombres):
        raise RegistroRechazado("las features del modelo no coinciden con las del extractor del servicio")
    if [int(c) for c in getattr(modelo, "classes_", [])] != [0, 1]:
        raise RegistroRechazado("el modelo no es el clasificador binario normal(0)/riesgo_preventivo(1)")


# ── Paridad con el servicio de inferencia ────────────────────────


def _opcional(valor) -> float | None:
    """Valor del CSV como lo trae una LecturaTermica: None si falta o no es finito."""
    if valor is None:
        return None
    numero = float(valor)
    return numero if math.isfinite(numero) else None


def _columna(df: pd.DataFrame, nombre: str) -> np.ndarray:
    return df[nombre].to_numpy(dtype=float) if nombre in df.columns else np.full(len(df), np.nan)


def _lecturas_para_servicio(df: pd.DataFrame, tiempos_us: np.ndarray) -> list[LecturaTermica]:
    """Las lecturas del CSV como las recibe el servicio desde la telemetría
    (nodo con hora sincronizada; la exportación ya excluye manuales y unsynced)."""
    device_id = str(df["serie_id"].iloc[0]).split("#", 1)[0]
    salida = []
    for t_us, interna, puerta, ambiente, humedad in zip(
        tiempos_us,
        _columna(df, "temperatura_interna"),
        _columna(df, "puerta_abierta"),
        _columna(df, "temperatura_ambiental"),
        _columna(df, "humedad_ambiental"),
        strict=True,
    ):
        salida.append(LecturaTermica(
            device_id=device_id,
            timestamp=_EPOCA + timedelta(microseconds=int(t_us)),
            temperatura_ambiental=_opcional(ambiente),
            humedad_ambiental=_opcional(humedad),
            temperatura_interna=_opcional(interna),
            apertura_refrigerador=bool(puerta >= 0.5) if math.isfinite(puerta) else None,
            estado_conectividad="online",
            time_quality="synced",
        ))
    return salida


def _vector_entrenamiento(
    df: pd.DataFrame, tiempos_us: np.ndarray, config: ConfiguracionPreventiva, conjunto: str
) -> np.ndarray | None:
    """Vector del extractor del ENTRENAMIENTO (`calidad.preparar_rejillas` +
    `features_de_rejillas`, como `corridas`) en la última lectura de `df`, o
    None si en ese punto el entrenamiento tampoco lo calcula (ventana
    incompleta): esa lectura no sirve para comparar."""
    referencia = df.assign(timestamp=pd.Series(pd.to_datetime(tiempos_us, unit="us", utc=True), index=df.index))
    try:
        rejillas = calidad.preparar_rejillas(referencia, config)
    except ValueError:
        return None
    x, ok = features_de_rejillas(rejillas, config, conjunto)
    fila = np.flatnonzero(rejillas["g_ns"].to_numpy() == int(tiempos_us[-1]) * 1000)
    return x[fila[0]] if fila.size == 1 and ok[fila[0]] else None


def _comparar_con_servicio(prediccion, referencia: np.ndarray, modelo, nombres: list[str], lugar: str) -> dict:
    """El servicio debe clasificar la lectura con el vector y la puntuación del
    entrenamiento. Devuelve el detalle o lanza RegistroRechazado (sin el
    prefijo del conjunto, que agrega quien llama)."""
    if prediccion.estado != CALIDAD_OK or not prediccion.vector_features:
        raise RegistroRechazado(
            f"en {lugar} el extractor del entrenamiento calcula las {len(nombres)} features, pero el servicio de "
            f"inferencia la deja sin clasificar ({prediccion.estado}: {prediccion.motivo}); el modelo registrado "
            "nunca clasificaría una lectura en producción"
        )
    servido = np.array([prediccion.vector_features.get(n, np.nan) for n in nombres], dtype=float)
    diferencias = np.abs(servido - referencia)
    diferencias = np.where(np.isfinite(diferencias), diferencias, np.inf)
    peor = int(np.argmax(diferencias))
    if diferencias[peor] > TOLERANCIA_PARIDAD:
        raise RegistroRechazado(
            f"en {lugar} el servicio de inferencia calcula {nombres[peor]} = {servido[peor]!r} y el entrenamiento "
            f"{float(referencia[peor])!r} (tolerancia {TOLERANCIA_PARIDAD:.1e})"
        )
    esperada = float(modelo.predict_proba(referencia.reshape(1, -1))[0, 1])
    if prediccion.puntuacion is None or abs(prediccion.puntuacion - esperada) > TOLERANCIA_PARIDAD:
        raise RegistroRechazado(
            f"en {lugar} el servicio de inferencia puntúa {prediccion.puntuacion!r} y el modelo, con el vector del "
            f"entrenamiento, {esperada!r}"
        )
    return {
        "comprobada": True,
        "estado_servicio": prediccion.estado,
        "features": len(nombres),
        "diferencia_max": float(diferencias.max()),
        "tolerancia": TOLERANCIA_PARIDAD,
    }


def comprobar_servicio(
    modelo,
    manifiesto: dict,
    lecturas: pd.DataFrame,
    corridas_prueba: list[str],
    config: ConfiguracionPreventiva,
) -> dict:
    """Paridad entrenamiento-servicio, ANTES de escribir en el registro.

    Registra el modelo en un registro temporal, lo carga en sombra con el
    servicio de inferencia y llama a `ServicioPrediccionPreventiva.predecir`
    (el camino de la telemetría) con las lecturas del CSV de una corrida de
    prueba como historial de LecturaTermica, hasta su lectura más reciente en
    rango cuya ventana el extractor del entrenamiento sí calcula. El servicio
    debe clasificarla (estado ok) con el vector y la puntuación del
    entrenamiento. Si el servicio no recibe una señal que usa el conjunto
    (como pasaba con el SHT31 de `termico_ambiente` antes de HU-17), deja la
    lectura sin clasificar y no se registra nada.

    La rejilla del servicio se ancla en la lectura y la del entrenamiento en
    múltiplos del paso desde la época: para compararlas, las lecturas se
    desplazan en bloque hasta que la elegida cae en un punto de la rejilla
    (las features solo dependen de tiempos relativos)."""
    from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA
    from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_SOMBRA, ServicioPrediccionPreventiva

    conjunto, version, nombres = manifiesto["conjunto_features"], manifiesto["version"], manifiesto["features"]

    def no_servible(detalle: str) -> RegistroRechazado:
        return RegistroRechazado(f"el conjunto {conjunto} no se puede servir: {detalle}")

    corrida = lecturas["serie_id"].astype(str).map(_corrida_de)
    de_prueba = lecturas[corrida.isin(corridas_prueba)]
    temperatura = de_prueba["temperatura_interna"]
    en_rango = temperatura.notna() & temperatura.map(lambda t: math.isfinite(t) and RANGO_TERMICO_BPA.contiene(t))
    candidatas = de_prueba[en_rango].sort_values("timestamp", ascending=False, kind="stable")
    por_serie = {serie: df.sort_values("timestamp", kind="stable") for serie, df in de_prueba.groupby("serie_id")}
    paso_us = config.paso_rejilla_s * 1_000_000

    with tempfile.TemporaryDirectory(prefix="coldcare-paridad-") as directorio:
        raiz_temporal = Path(directorio)
        try:
            registro.registrar(modelo, manifiesto, raiz_temporal)
        except registro.ErrorRegistro as exc:
            raise no_servible(f"no se pudo preparar la comprobación ({exc})") from None
        sha256 = registro.leer_manifiesto(version, raiz_temporal)["artefacto"]["sha256"]
        servicio = ServicioPrediccionPreventiva(MODO_SOMBRA, version, sha256, raiz=raiz_temporal)
        if not servicio.disponible:
            raise no_servible(
                f"el servicio de inferencia no carga el modelo en sombra ({servicio.motivo_codigo()}: "
                f"{servicio.motivo_rechazo()})"
            )
        for _, fila in candidatas.head(MAX_CANDIDATAS_PARIDAD).iterrows():
            serie = por_serie[fila["serie_id"]]
            instante = fila["timestamp"]
            ventana = serie[(serie["timestamp"] <= instante) & (serie["timestamp"] > instante - HISTORIAL_PARIDAD)]
            tiempos_us = a_epoch_ns(ventana["timestamp"]) // 1000
            tiempos_us = tiempos_us + (-int(tiempos_us[-1])) % paso_us
            referencia = _vector_entrenamiento(ventana, tiempos_us, config, conjunto)
            if referencia is None:
                continue
            historial = _lecturas_para_servicio(ventana, tiempos_us)
            prediccion = servicio.predecir(historial[-1], historial[:-1])
            corrida_id = _corrida_de(str(fila["serie_id"]))
            lugar = f"la lectura {instante.isoformat()} de la corrida de prueba {corrida_id}"
            try:
                detalle = _comparar_con_servicio(prediccion, referencia, modelo, nombres, lugar)
            except RegistroRechazado as exc:
                raise no_servible(str(exc)) from None
            return {**detalle, "corrida": corrida_id, "lectura_utc": instante.isoformat()}
    raise no_servible(
        "ninguna lectura en rango de las corridas de prueba tiene una ventana que el extractor del entrenamiento "
        "calcule, así que no se puede comparar el servicio con el entrenamiento"
    )


# ── Manifiesto ───────────────────────────────────────────────────


def _corrida_de(serie_id: str) -> str:
    return serie_id.split("#", 1)[1] if "#" in serie_id else serie_id


def _rol(corrida: str, particiones: dict) -> str:
    if corrida in particiones["prueba"]:
        return "prueba"
    if corrida in particiones["entrenamiento"]:
        return "entrenamiento"
    if corrida in particiones["validacion"]:
        return "validacion"
    if corrida in particiones["descartadas"]:
        return "descartada"
    return "sin_observaciones_validas"


def procedencia_datos(ruta_csv: Path, informe: dict, particiones: dict) -> dict:
    """Qué datos entrenaron el modelo: el archivo exportado (nombre, SHA-256 y
    tamaño, nunca la ruta local), las corridas con su rol y su rango de fechas."""
    lecturas: pd.DataFrame = informe["_lecturas"]
    corridas = []
    for serie_id, df in lecturas.groupby("serie_id", sort=True):
        corrida = _corrida_de(str(serie_id))
        corridas.append({
            "corrida_id": corrida,
            "device_id": str(serie_id).split("#", 1)[0],
            "rol": _rol(corrida, particiones),
            "desde_utc": df["timestamp"].min().isoformat(),
            "hasta_utc": df["timestamp"].max().isoformat(),
            "lecturas": len(df),
        })
    return {
        "archivo": ruta_csv.name,
        "sha256": registro.sha256_archivo(ruta_csv),
        "bytes": ruta_csv.stat().st_size,
        "exportador": "scripts/exportar_corridas.py (P-1)",
        "filas_leidas": informe["importacion"]["filas_leidas"],
        "filas_validas": informe["importacion"]["filas_validas"],
        "descartes": informe["importacion"]["descartes"],
        "desde_utc": lecturas["timestamp"].min().isoformat(),
        "hasta_utc": lecturas["timestamp"].max().isoformat(),
        "corridas": corridas,
        "incidentes_independientes": informe["incidentes_independientes"],
    }


def construir_manifiesto(
    version: str,
    conjunto: str,
    config: ConfiguracionPreventiva,
    resultado: dict,
    datos: dict,
    sklearn_fijado: str,
) -> dict:
    from src.infrastructure.ai.prediccion_preventiva.experimentos import versiones_entorno

    modelo = resultado["_modelo"]
    seleccion = resultado["seleccion_umbral"]
    rf = resultado["prueba"]["random_forest"]
    return {
        "version": version,
        "algoritmo": "sklearn.ensemble.RandomForestClassifier",
        "tarea": "clasificación preventiva binaria normal(0) / riesgo_preventivo(1)",
        "clases": {"0": "normal", "1": "riesgo_preventivo"},
        "entrenado_en": datetime.now(tz=timezone.utc).isoformat(),
        "estado": ESTADO_EXPERIMENTAL,
        "motivo_estado": (
            "Registrado desde corridas controladas (P-2). Pasa a VALIDATED_OFFLINE solo si los criterios "
            "se revisan con la regla de cuatro ojos."
        ),
        "fuente_entrenamiento": FUENTE_CORRIDAS,
        "datos": datos,
        "conjunto_features": conjunto,
        "features": list(resultado["_nombres"]),
        "configuracion": config.como_dict(),
        "hiperparametros": sin_privados(modelo.get_params()),
        "semilla": SEMILLA,
        "umbral_decision": float(seleccion["umbral"]),
        "origen_umbral": DESCRIPCION_METODO_UMBRAL[seleccion["metodo"]],
        "seleccion_umbral": sin_privados(seleccion),
        "particiones": sin_privados(resultado["particiones"]),
        "puntuacion_calibrada": False,
        "versiones": versiones_entorno(),
        "requisitos": {"archivo": "requirements.txt", "scikit_learn_fijado": sklearn_fijado},
        "metricas_prueba": sin_privados({
            "average_precision": rf["average_precision"],
            "macro_f1": rf["observacion"]["macro_f1"],
            "recall_riesgo": rf["observacion"]["por_clase"]["riesgo_preventivo"]["recall"],
            "precision_riesgo": rf["observacion"]["por_clase"]["riesgo_preventivo"]["precision"],
            "matriz_confusion": rf["observacion"]["matriz_confusion"],
            "sensibilidad_por_incidente": rf["evento"].get("sensibilidad_por_incidente"),
            "falsas_alarmas_por_dia_sensor": rf["evento"].get("falsas_alarmas_por_dia_sensor"),
            "anticipacion_min": rf["evento"].get("anticipacion_min"),
        }),
        "metricas_registro": sin_privados(resultado["metricas_registro"]),
        "criterios_aceptacion": sin_privados(resultado["criterios"]),
    }


def cuerpo_registro_api(manifiesto: dict) -> dict:
    """Cuerpo de POST /api/ia/modelo/versiones con los valores del manifiesto
    (paso 6 de IA_PREVENTIVA_ACTIVACION.md): /activar rechaza un horizonte, una
    versión de scikit-learn o un esquema de features distintos. Las métricas
    son las de la prueba; se revisan con la regla de cuatro ojos."""
    particiones = manifiesto["particiones"]
    cuerpo_particiones = {
        "entrenamiento": ",".join(particiones["entrenamiento"]),
        "validacion": ",".join(particiones["validacion"]),
        "prueba": ",".join(particiones["prueba"]),
        "metodo_umbral": particiones["metodo_umbral"],
    }
    if particiones.get("separacion_min") is not None:
        cuerpo_particiones["separacion_min"] = particiones["separacion_min"]
    return {
        "version": manifiesto["version"],
        "model_hash": manifiesto["artefacto"]["sha256"],
        "feature_schema_version": manifiesto["conjunto_features"],
        "dataset_version": manifiesto["datos"]["dataset_version"],
        "scikit_learn_version": manifiesto["versiones"]["scikit_learn"],
        "python_version": manifiesto["versiones"]["python"],
        "trained_at": manifiesto["entrenado_en"],
        "horizonte_min": manifiesto["configuracion"]["horizonte_min"],
        # El registro de la base no admite métricas nulas: se omiten las no estimables.
        "metricas": {k: v for k, v in manifiesto["metricas_registro"].items() if v is not None},
        "particiones": cuerpo_particiones,
    }


def advertencias_registro(particiones: dict) -> list[str]:
    """Lo que no impide registrar (la versión sirve en sombra) pero sí activar
    después. Sin corridas de ajuste o validación del mismo equipo que las de
    prueba no hay separación que medir: `cuerpo_registro_api` va sin
    `separacion_min`, que POST /activar exige (CAMPOS_PARTICIONES)."""
    advertencias = []
    if particiones.get("separacion_min") is None:
        advertencias.append(
            "ninguna corrida de ajuste o de validación es del mismo equipo que las de prueba, así que no hay "
            "separación medible (separacion_min): cuerpo_registro_api va sin ella y POST /activar responderá "
            "409 faltan_particiones. Para poder activar, registre una versión nueva que ajuste con al menos una "
            "corrida del equipo de prueba separada al menos H de las de prueba; no escriba una separación a mano"
        )
    return advertencias


# ── Registro ─────────────────────────────────────────────────────


def _verificar_registrado(version: str, sha256: str, raiz: Path) -> dict:
    """Relee lo escrito como lo leerán la carga y /activar."""
    from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_SOMBRA, ServicioPrediccionPreventiva

    try:
        registro.cargar_verificado(version, raiz, sha256)
    except registro.ErrorRegistro as exc:
        raise RegistroRechazado(
            f"la versión {version} quedó escrita pero no pasa la verificación ({exc}); márquela "
            "REJECTED_INSUFFICIENT_EVIDENCE con `estado` y no la despliegue"
        ) from None
    sha_manifiesto = registro.sha256_archivo(registro.ruta_manifiesto(version, raiz))
    sombra = ServicioPrediccionPreventiva(MODO_SOMBRA, version, sha256, raiz=raiz)
    verificacion = {
        "sha256_artefacto_coincide": True,
        "manifiesto_anotado_en_historial": sha_manifiesto == registro.sha256_manifiesto_en_historial(version, raiz),
        "carga_en_sombra": sombra.disponible,
        "motivo_no_disponible_en_sombra": sombra.motivo_codigo(),
    }
    if not (verificacion["manifiesto_anotado_en_historial"] and verificacion["carga_en_sombra"]):
        raise RegistroRechazado(
            f"la versión {version} quedó escrita pero no pasa la verificación ({verificacion}); márquela "
            "REJECTED_INSUFFICIENT_EVIDENCE con `estado` y no la despliegue"
        )
    return verificacion


def _ruta_visible(destino: Path) -> str:
    try:
        return destino.relative_to(RAIZ_BACKEND).as_posix()
    except ValueError:
        return str(destino)


def registrar_desde_corridas(
    ruta_csv: Path,
    corridas_prueba: list[str],
    version: str,
    conjunto: str = CONJUNTO_TERMICO,
    config: ConfiguracionPreventiva | None = None,
    corridas_validacion: list[str] | None = None,
    raiz: Path | None = None,
    dataset_version: str | None = None,
    requisitos: Path | None = None,
) -> dict:
    """Entrena con las corridas, registra la versión y devuelve el resumen (con
    `cuerpo_registro_api` y, en `_informe`, el informe completo de corridas).
    Lanza RegistroRechazado sin escribir nada si algo no cuadra; la única
    negativa posterior a escribir es la de la verificación final, que no
    debería ocurrir porque `_exigir_compatible` ya comprobó lo mismo."""
    config = config or ConfiguracionPreventiva()
    raiz = raiz or registro.RAIZ_REGISTRO
    # Comprobaciones baratas primero: no se entrena si no se va a poder registrar.
    sklearn_fijado = exigir_sklearn_fijado(requisitos)
    exigir_version_nueva(version, raiz)
    if conjunto not in CONJUNTOS:
        raise RegistroRechazado(f"conjunto de features desconocido: {conjunto} ({', '.join(CONJUNTOS)})")
    if dataset_version is not None and not 1 <= len(dataset_version) <= LONGITUD_MAX_DATASET:
        raise RegistroRechazado(f"dataset_version debe tener entre 1 y {LONGITUD_MAX_DATASET} caracteres")
    if not corridas_prueba:
        raise RegistroRechazado("hace falta al menos una corrida de prueba (--prueba)")
    if not ruta_csv.is_file():
        raise RegistroRechazado(f"no existe el CSV de corridas {ruta_csv.name}")

    try:
        # Solo el conjunto que se registra es obligatorio: la ablación con el
        # SHT31 queda "no estimable" si sus corridas no lo tienen, sin impedir
        # registrar un conjunto que no lo usa.
        informe = ejecutar_pipeline_corridas(
            ruta_csv, corridas_prueba, config, corridas_validacion, (conjunto,), obligatorios=(conjunto,)
        )
    except (ValueError, OSError) as exc:
        raise RegistroRechazado(f"las corridas no se pueden usar: {exc}") from None
    resultado = informe["resultados"][conjunto]
    _exigir_datos_suficientes(resultado, conjunto)
    modelo = resultado["_modelo"]
    _exigir_compatible(modelo, resultado["_nombres"], config, conjunto)

    datos = procedencia_datos(ruta_csv, informe, resultado["particiones"])
    # Propuesta para P-4: la versión del dataset es la huella del CSV exportado.
    datos["dataset_version"] = dataset_version or f"corridas-{datos['sha256'][:12]}"
    manifiesto = construir_manifiesto(version, conjunto, config, resultado, datos, sklearn_fijado)
    # Antes de escribir: el servicio debe poder clasificar con este conjunto.
    paridad = comprobar_servicio(modelo, manifiesto, informe["_lecturas"], resultado["particiones"]["prueba"], config)
    advertencias = advertencias_registro(resultado["particiones"])
    try:
        destino = registro.registrar(modelo, manifiesto, raiz)
    except registro.ErrorRegistro as exc:
        raise RegistroRechazado(str(exc)) from None

    escrito = registro.leer_manifiesto(version, raiz)
    sha256 = escrito["artefacto"]["sha256"]
    verificacion = _verificar_registrado(version, sha256, raiz)
    return {
        "version": version,
        "directorio": _ruta_visible(destino),
        "estado": escrito["estado"],
        "sha256": sha256,
        "sha256_manifiesto": registro.sha256_archivo(registro.ruta_manifiesto(version, raiz)),
        "conjunto_features": conjunto,
        "horizonte_min": escrito["configuracion"]["horizonte_min"],
        "umbral_decision": escrito["umbral_decision"],
        "metodo_umbral": escrito["seleccion_umbral"]["metodo"],
        "particiones": escrito["particiones"],
        "datos": {k: escrito["datos"][k] for k in ("archivo", "sha256", "dataset_version", "desde_utc", "hasta_utc")},
        "criterios_aceptacion": escrito["criterios_aceptacion"],
        "verificacion": verificacion,
        "paridad_servicio": paridad,
        "cuerpo_registro_api": cuerpo_registro_api(escrito),
        "advertencias": advertencias,
        "_informe": informe,
    }
