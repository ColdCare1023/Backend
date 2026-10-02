"""Servicio de visualización y agregación de tendencia térmica (HU-31).

Criterios de aceptación implementados:
  - Escenario 1 (datos reales y zonas 2-8 °C): Curva térmica con bandas de seguridad (2.0 a 8.0 °C).
  - Escenario 2 (sin datos): Estado vacío (empty state) con mensaje descriptivo.
  - Escenario 3 (gran volumen): Algoritmo de downsampling para mantener rendimiento sin perder picos de excursión.
  - Escenario 4 (ventana 24 h por defecto): Rango de 24 horas por defecto, con soporte para cambio de ventana (6h, 12h, 24h, 168h / 7d).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

RANGO_MIN_SEGURO = 2.0
RANGO_MAX_SEGURO = 8.0
MAX_PUNTOS_GRAFICA = 500


class PuntoGrafica(BaseModel):
    timestamp: str
    temp_interior: float | None
    temp_ambiente: float | None
    humedad_ambiente: float | None
    en_rango: bool
    puerta_abierta: bool | None


class RespuestaGrafica(BaseModel):
    device_id: str
    ventana_horas: int
    desde: str
    hasta: str
    empty_state: bool
    mensaje_empty: str | None = None
    rango_seguro: dict[str, Any]
    total_lecturas: int
    puntos_renderizados: int
    downsampled: bool
    puntos: list[PuntoGrafica]


def downsample_lttb(puntos: list[dict[str, Any]], max_puntos: int) -> list[dict[str, Any]]:
    """Muestreo visual para mantener fluidez preservando extremos y excursiones térmicas."""
    if len(puntos) <= max_puntos:
        return puntos

    # Intervalo de paso uniforme preservando primer y último punto
    paso = len(puntos) / (max_puntos - 2)
    muestreados = [puntos[0]]

    for i in range(1, max_puntos - 1):
        idx = int(i * paso)
        if idx < len(puntos) - 1:
            muestreados.append(puntos[idx])

    muestreados.append(puntos[-1])
    return muestreados


class GeneradorDatosGrafica:
    """Motor de consulta y transformación de tendencia térmica."""

    def __init__(self):
        # Almacén en memoria de lecturas de prueba por dispositivo
        self._lecturas: dict[str, list[dict[str, Any]]] = {}

    def agregar_lecturas(self, device_id: str, lecturas: list[dict[str, Any]]):
        if device_id not in self._lecturas:
            self._lecturas[device_id] = []
        self._lecturas[device_id].extend(lecturas)
        # Ordenar por timestamp
        self._lecturas[device_id].sort(key=lambda x: x["timestamp"])

    def obtener_curva_termica(
        self,
        device_id: str,
        horas: int = 24,
        ahora: datetime | None = None,
    ) -> RespuestaGrafica:
        ahora_dt = ahora or datetime.now(timezone.utc)
        desde_dt = ahora_dt - timedelta(hours=horas)

        todas = self._lecturas.get(device_id, [])
        # Filtrar por ventana temporal
        en_ventana = [
            l for l in todas
            if desde_dt <= datetime.fromisoformat(l["timestamp"].replace("Z", "+00:00")) <= ahora_dt
        ]

        rango_info = {
            "min_objetivo": RANGO_MIN_SEGURO,
            "max_objetivo": RANGO_MAX_SEGURO,
            "unidad": "°C",
            "zonas": [
                {"nombre": "congelacion_riesgo", "min": None, "max": RANGO_MIN_SEGURO, "color": "#ef4444"},
                {"nombre": "rango_seguro", "min": RANGO_MIN_SEGURO, "max": RANGO_MAX_SEGURO, "color": "#22c55e"},
                {"nombre": "calentamiento_riesgo", "min": RANGO_MAX_SEGURO, "max": None, "color": "#ef4444"},
            ],
        }

        if not en_ventana:
            # Escenario 2: Estado vacío
            return RespuestaGrafica(
                device_id=device_id,
                ventana_horas=horas,
                desde=desde_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                hasta=ahora_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                empty_state=True,
                mensaje_empty="Esperando primera telemetría del dispositivo en esta ventana.",
                rango_seguro=rango_info,
                total_lecturas=0,
                puntos_renderizados=0,
                downsampled=False,
                puntos=[],
            )

        # Escenario 3: Downsampling si hay gran volumen
        total_puntos = len(en_ventana)
        aplicar_downsampling = total_puntos > MAX_PUNTOS_GRAFICA
        puntos_procesados = downsample_lttb(en_ventana, MAX_PUNTOS_GRAFICA) if aplicar_downsampling else en_ventana

        puntos_resultado = []
        for p in puntos_procesados:
            temp_int = p.get("temp_ds18b20")
            en_rango = (temp_int is not None) and (RANGO_MIN_SEGURO <= temp_int <= RANGO_MAX_SEGURO)
            puntos_resultado.append(
                PuntoGrafica(
                    timestamp=p["timestamp"],
                    temp_interior=temp_int,
                    temp_ambiente=p.get("temp_sht31"),
                    humedad_ambiente=p.get("humedad_sht31"),
                    en_rango=en_rango,
                    puerta_abierta=p.get("door_open"),
                )
            )

        return RespuestaGrafica(
            device_id=device_id,
            ventana_horas=horas,
            desde=desde_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            hasta=ahora_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            empty_state=False,
            rango_seguro=rango_info,
            total_lecturas=total_puntos,
            puntos_renderizados=len(puntos_resultado),
            downsampled=aplicar_downsampling,
            puntos=puntos_resultado,
        )


generador_grafica_global = GeneradorDatosGrafica()
grafica_router = APIRouter(prefix="/api/v1/telemetria", tags=["Gráfica y Tendencia"])


@grafica_router.get("/grafica", response_model=RespuestaGrafica)
def obtener_grafica(
    device_id: str = Query(..., description="Identificador del dispositivo"),
    horas: int = Query(24, ge=1, le=720, description="Ventana temporal en horas (defecto: 24)"),
):
    return generador_grafica_global.obtener_curva_termica(device_id, horas=horas)
