"""Criterios del backlog que la verificación del 8/11/2026 encontró incumplidos."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.random_forest_service import RandomForestRiesgoService
from src.infrastructure.mqtt import mqtt_client
from src.infrastructure.pdf.generador_pdf import GeneradorReporteBPAPDF
from src.interface.api.schemas import AccionCorrectivaCreateRequest

AHORA = datetime.now(tz=timezone.utc)


def _textos(flowables) -> str:
    salida = []
    for f in flowables:
        for fila in getattr(f, "_cellvalues", []):
            salida.extend(getattr(c, "text", str(c)) for c in fila)
        if hasattr(f, "text"):
            salida.append(f.text)
    return " ".join(salida)


def test_hu46_no_carga_si_el_artefacto_no_es_el_de_la_version_activa():
    servicio = RandomForestRiesgoService()
    servicio.fijar_hash_aprobado("b" * 64)
    assert "versión activa" in (servicio.motivo_rechazo() or "")


def test_hu46_carga_si_el_hash_coincide():
    servicio = RandomForestRiesgoService()
    servicio.fijar_hash_aprobado(servicio.hash_artefacto())
    assert servicio.motivo_rechazo() is None


def _lectura(minutos: int = 0, time_quality: str | None = "sntp_synced") -> LecturaTermica:
    return LecturaTermica(
        device_id="ESP32-HU18",
        temperatura_interna=5.0,
        temperatura_ambiental=20.0,
        humedad_ambiental=60.0,
        apertura_refrigerador=False,
        estado_conectividad="conectado",
        timestamp=AHORA - timedelta(minutes=minutos),
        time_quality=time_quality,
    )


def test_hu18_sin_hora_sincronizada_no_se_clasifica():
    caso = ClasificarRiesgoTermicoUseCase(RandomForestRiesgoService(), "experimental")
    historial = [_lectura(m) for m in range(1, 12)]

    resultado = caso.execute(_lectura(time_quality="unsynced"), historial)

    assert resultado.nivel is None
    assert resultado.motivo_no_inferencia == "time_quality_insuficiente"


class _Mensaje:
    def __init__(self, equipo: str, orden: int) -> None:
        self.topic = f"farmacias/{equipo}/lecturas"
        self.orden = orden


class _Cliente:
    def __init__(self, mensajes) -> None:
        self._mensajes = mensajes

    @property
    def messages(self):
        async def generador():
            for mensaje in self._mensajes:
                yield mensaje

        return generador()


@pytest.mark.asyncio
async def test_hu12_un_equipo_lento_no_retrasa_a_otro_y_cada_uno_conserva_su_orden():
    liberar = asyncio.Event()
    atendidos: list[tuple[str, int]] = []

    async def manejador(_cliente, mensaje):
        equipo = mensaje.topic.split("/")[1]
        if equipo == "LENTO" and mensaje.orden == 1:
            await liberar.wait()
        atendidos.append((equipo, mensaje.orden))
        if equipo == "RAPIDO":
            liberar.set()

    mensajes = [_Mensaje("LENTO", 1), _Mensaje("LENTO", 2), _Mensaje("RAPIDO", 1)]
    await asyncio.wait_for(mqtt_client.consumir_mensajes(_Cliente(mensajes), manejador), 5)

    assert atendidos.index(("RAPIDO", 1)) < atendidos.index(("LENTO", 1))
    assert atendidos.index(("LENTO", 1)) < atendidos.index(("LENTO", 2))


def _alerta(nivel: str, n: int) -> dict:
    return {
        "created_at": AHORA - timedelta(minutes=n), "device_id": "E1", "nivel_riesgo": nivel,
        "mensaje": f"alerta-{nivel}-{n}", "revisada": False, "episodio_abierto": None,
    }


def test_hu38_ninguna_excursion_critica_se_omite():
    generador = GeneradorReporteBPAPDF()
    alertas = [_alerta("riesgo_preventivo", n) for n in range(90)] + [
        _alerta("excursion_critica", n) for n in range(100, 250)
    ]

    texto = _textos(generador._seccion_alertas(alertas))

    assert all(f"alerta-excursion_critica-{n}" in texto for n in range(100, 250))
    assert "todas las excursiones críticas (150)" in texto


def test_hu38_el_pdf_incluye_las_acciones_correctivas():
    texto = _textos(
        GeneradorReporteBPAPDF()._seccion_acciones(
            [{"created_at": AHORA, "device_id": "E1", "decision": "Inmovilizar", "descripcion": "Se movió el lote",
              "justificacion": "Superó 8 °C", "vigente": True}]
        )
    )
    assert "Inmovilizar" in texto and "Se movió el lote" in texto and "Superó 8 °C" in texto


def test_hu51_el_reporte_incluye_ubicacion_y_sus_cambios():
    texto = _textos(
        GeneradorReporteBPAPDF()._seccion_equipos(
            [{"id": "E1", "ubicacion": "Estante 2", "fecha_instalacion": "2026-08-01",
              "observaciones_instalacion": None,
              "cambios_ubicacion": [{"valor_anterior": "Estante 1", "valor_nuevo": "Estante 2", "created_at": AHORA}]}]
        )
    )
    assert "Estante 2" in texto and "cambió de «Estante 1»" in texto


def test_hu27_exige_decision_y_justificacion():
    with pytest.raises(ValidationError):
        AccionCorrectivaCreateRequest(descripcion="Se movió el lote")
    AccionCorrectivaCreateRequest(decision="Inmovilizar", descripcion="Se movió el lote", justificacion="Superó 8 °C")
