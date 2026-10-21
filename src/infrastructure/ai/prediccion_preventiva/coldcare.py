"""Experimento D: evaluación de una versión registrada con mediciones reales
del ESP32/DS18B20 de ColdCare (exportadas de `thermal_readings`).

Usa EXACTAMENTE el mismo pipeline que los datasets externos (limpieza,
rejilla, episodios, etiquetas, features) y la configuración y el umbral
guardados en el manifiesto de la versión. No reentrena nada.
"""

from pathlib import Path

import numpy as np

from src.infrastructure.ai.prediccion_preventiva import calidad, eventos, ingesta, registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import configuracion_desde_manifiesto
from src.infrastructure.ai.prediccion_preventiva.entrenamiento import construir_observaciones
from src.infrastructure.ai.prediccion_preventiva.etiquetas import etiquetar
from src.infrastructure.ai.prediccion_preventiva.evaluacion import metricas_evento, metricas_observacion


def evaluar_coldcare(ruta_csv: Path, version: str, raiz: Path = registro.RAIZ_REGISTRO) -> dict:
    modelo, manifiesto = registro.cargar_verificado(version, raiz)
    config = configuracion_desde_manifiesto(manifiesto)
    crudas, informe = ingesta.importar_coldcare_csv(ruta_csv)
    lecturas, limpieza = calidad.limpiar(crudas)
    rejillas = calidad.preparar_rejillas(lecturas, config)
    episodios = eventos.agrupar_incidentes(eventos.detectar_episodios(rejillas, config), config)
    obs = construir_observaciones(etiquetar(rejillas, episodios, config), config, manifiesto["conjunto_features"])
    if obs.nombres != manifiesto["features"]:
        raise ValueError("Las features del extractor no coinciden con las del manifiesto")
    informe_base = {
        "version": version,
        "importacion": informe.como_dict(),
        "limpieza": limpieza.como_dict(),
        "series": sorted(lecturas["serie_id"].unique().tolist()),
        "observaciones": len(obs.tabla),
        "positivas": int(obs.y.sum()) if len(obs.tabla) else 0,
        "incidentes_independientes": int(episodios["incidente_id"].nunique()) if len(episodios) else 0,
        "observaciones_excluidas": obs.excluidas,
    }
    if len(obs.tabla) == 0:
        return {**informe_base, "resultado": "no estimable: sin observaciones elegibles"}
    puntuacion = modelo.predict_proba(obs.x)[:, 1]
    umbral = manifiesto["umbral_decision"]
    return {
        **informe_base,
        "resultado": "evaluado",
        "observacion": metricas_observacion(obs.y, puntuacion, umbral, True),
        "evento": metricas_evento(obs.tabla, puntuacion >= umbral, episodios, config),
        "alarmas_totales": int(np.sum(puntuacion >= umbral)),
    }
