"""QA & Integration Test Suite for HU-12: Continuous Telemetry Reception in Backend.

Checklist items covered:
  - [BE] Verificar suscripción al iniciar el servicio
  - [QA] Probar varios dispositivos simultáneos
  - [QA] Probar reconexión tras corte del broker
  - [QA] Probar API y consumo MQTT en paralelo
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from consumidor_mqtt import ConsumidorMQTT, app, consumidor_global

EJEMPLOS = Path(__file__).parent.parent / "contrato" / "ejemplos"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_hu12_be_suscripcion_al_iniciar_servicio(client):
    """[BE] Al arrancar el servicio FastAPI, el consumidor inicia con client_id fijo y clean_session=False."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "healthy"
    assert data["mqtt_connected"] is True
    assert data["client_id"] == "backend-consumer-prod"
    assert data["clean_session"] is False


def test_hu12_qa_varios_dispositivos_simultaneos():
    """[QA] Concurrencia de múltiples dispositivos IoT enviando telemetría simultáneamente."""
    consumidor = ConsumidorMQTT(client_id="test-consumer-multi")
    consumidor.start()

    dispositivos = ["ESP32_FARMACIA_01", "ESP32_FARMACIA_02", "ESP32_FARMACIA_03", "ESP32_DEMO_01"]
    base_raw = json.loads((EJEMPLOS / "telemetria_normal.json").read_text(encoding="utf-8"))

    total_por_dispositivo = 10
    mensajes = []
    for dev in dispositivos:
        for seq in range(1, total_por_dispositivo + 1):
            m = dict(base_raw)
            m["device_id"] = dev
            m["seq_no"] = seq
            m["boot_id"] = 1
            mensajes.append((f"farmacias/{dev}/lecturas", json.dumps(m).encode("utf-8")))

    # Enviar mensajes de forma altamente concurrente a través de un pool de hilos
    def enviar(m_tuple):
        t, b = m_tuple
        return consumidor.process_message(t, b)

    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(enviar, mensajes))

    assert len(resultados) == len(dispositivos) * total_por_dispositivo
    assert all(r["status"] == "committed" for r in resultados)

    health = consumidor.get_health_status()
    assert health["active_devices_count"] == len(dispositivos)
    assert health["total_committed"] == len(mensajes)
    assert health["active_devices"] == sorted(dispositivos)
    consumidor.stop()


def test_hu12_qa_reconexion_tras_corte_del_broker():
    """[QA] Simulación de corte y reanudación con sesión persistente."""
    consumidor = ConsumidorMQTT(client_id="test-consumer-reconn")
    consumidor.start()
    assert consumidor.get_health_status()["status"] == "healthy"

    # Simular caída del broker
    consumidor.simulate_broker_disconnect()
    health_caida = consumidor.get_health_status()
    assert health_caida["status"] == "degraded"
    assert health_caida["mqtt_connected"] is False

    # Simular reconexión automática exitosa
    consumidor.simulate_broker_reconnect()
    health_reanudado = consumidor.get_health_status()
    assert health_reanudado["status"] == "healthy"
    assert health_reanudado["mqtt_connected"] is True
    assert health_reanudado["reconnect_count"] == 1
    consumidor.stop()


def test_hu12_qa_api_y_consumo_mqtt_en_paralelo(client):
    """[QA] La API HTTP responde con baja latencia mientras el consumidor MQTT procesa flujo de datos."""
    base_raw = json.loads((EJEMPLOS / "telemetria_normal.json").read_text(encoding="utf-8"))
    stop_event = threading.Event()
    mqtt_processed = []

    def generar_trafico_mqtt():
        seq = 1
        while not stop_event.is_set() and seq <= 50:
            m = dict(base_raw)
            m["seq_no"] = 1000 + seq
            consumidor_global.process_message("farmacias/ESP32_FARMACIA_01/lecturas", json.dumps(m).encode("utf-8"))
            mqtt_processed.append(seq)
            seq += 1

    thread_mqtt = threading.Thread(target=generar_trafico_mqtt)
    thread_mqtt.start()

    # Realizar llamadas HTTP concurrentes
    respuestas = []
    for _ in range(20):
        resp = client.get("/health")
        respuestas.append(resp.status_code)

    stop_event.set()
    thread_mqtt.join()

    assert all(code == 200 for code in respuestas)
    assert len(mqtt_processed) > 0

    health = client.get("/health").json()
    assert health["total_received"] >= len(mqtt_processed)
    assert health["last_message_at"] is not None
