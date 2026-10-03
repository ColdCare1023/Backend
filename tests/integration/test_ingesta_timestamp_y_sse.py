"""B-10: validación del instante declarado por el dispositivo.
B-06: la ingesta por HTTP también notifica al dashboard vía SSE.

Aceptar un timestamp futuro permitiría insertar registros que desplazan el
orden temporal de la evidencia térmica; rechazarlo es un control de integridad,
no una validación de formato."""

from datetime import datetime, timedelta, timezone

from src.domain.entities.lectura_termica import LecturaTermica
from tests.conftest import auth_header

AHORA = datetime.now(tz=timezone.utc)


def _payload(timestamp: datetime, **overrides) -> dict:
    base = {
        "device_id": "ESP32-TS-01",
        "timestamp": timestamp.isoformat(),
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0,
        "temperatura_interna": 5.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }
    return {**base, **overrides}


# ── Unitarias de la regla de dominio ──────────────────────────────────────
def test_timestamp_actual_es_valido():
    valido, motivo = LecturaTermica.es_timestamp_valido(AHORA, ahora=AHORA)
    assert valido is True
    assert motivo == "ok"


def test_deriva_pequena_del_reloj_del_dispositivo_se_tolera():
    """Un ESP32 sin NTP puede adelantarse unos minutos; eso no es un ataque."""
    valido, _ = LecturaTermica.es_timestamp_valido(AHORA + timedelta(minutes=5), ahora=AHORA)
    assert valido is True


def test_timestamp_futuro_se_rechaza():
    valido, motivo = LecturaTermica.es_timestamp_valido(AHORA + timedelta(hours=3), ahora=AHORA)
    assert valido is False
    assert motivo == "timestamp_futuro"


def test_reenvio_de_lecturas_almacenadas_tras_caida_de_red_se_acepta():
    """Escenario real: el nodo pierde WiFi por la noche y vuelca su buffer al
    reconectar. Esas lecturas son válidas y no deben descartarse."""
    valido, _ = LecturaTermica.es_timestamp_valido(AHORA - timedelta(hours=10), ahora=AHORA)
    assert valido is True


def test_timestamp_absurdamente_antiguo_se_rechaza():
    valido, motivo = LecturaTermica.es_timestamp_valido(AHORA - timedelta(days=30), ahora=AHORA)
    assert valido is False
    assert motivo == "timestamp_demasiado_antiguo"


def test_timestamp_naive_se_interpreta_como_utc():
    """SQLite devuelve datetimes sin zona; el criterio es el mismo que usa la
    canonicalización de la cadena hash."""
    naive = AHORA.replace(tzinfo=None)
    valido, _ = LecturaTermica.es_timestamp_valido(naive, ahora=AHORA)
    assert valido is True


# ── De extremo a extremo por la API ───────────────────────────────────────
def test_api_rechaza_lectura_con_timestamp_futuro(client, token_tecnico):
    respuesta = client.post(
        "/api/lecturas",
        json=_payload(AHORA + timedelta(hours=6)),
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 422
    assert "timestamp_futuro" in respuesta.json()["detail"]


def test_rechazo_por_timestamp_queda_auditado(client, token_tecnico, token_admin):
    """El rechazo debe dejar rastro: un dispositivo que insiste en enviar
    timestamps futuros es una señal a investigar, no un error a silenciar."""
    client.post(
        "/api/lecturas",
        json=_payload(AHORA + timedelta(hours=6)),
        headers=auth_header(token_tecnico),
    )
    auditoria = client.get("/api/auditoria", headers=auth_header(token_admin)).json()
    entradas = [e for e in auditoria if e["accion"] == "LECTURA_RECHAZADA_TIMESTAMP"]
    assert len(entradas) == 1
    assert entradas[0]["detalle"]["motivo"] == "timestamp_futuro"


def test_lectura_rechazada_no_se_persiste_ni_encadena(client, token_tecnico, token_admin):
    respuesta = client.post(
        "/api/lecturas",
        json=_payload(AHORA + timedelta(hours=6)),
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 422

    lecturas = client.get("/api/lecturas", headers=auth_header(token_tecnico)).json()
    assert not any(lec["device_id"] == "ESP32-TS-01" for lec in lecturas)

    registros = client.get("/api/trazabilidad", headers=auth_header(token_admin)).json()
    assert not any(r.get("device_id") == "ESP32-TS-01" for r in registros)


def test_api_acepta_lectura_reciente(client, token_tecnico):
    respuesta = client.post(
        "/api/lecturas",
        json=_payload(AHORA - timedelta(minutes=2)),
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 201


# ── B-06: SSE desde el camino HTTP ────────────────────────────────────────
def test_ingesta_http_publica_evento_sse(client, token_tecnico, app):
    """Antes solo el camino MQTT emitía SSE: una lectura enviada por REST se
    guardaba sin que ninguna pantalla abierta se enterara."""
    publicados: list[tuple[dict, str]] = []

    broadcaster = app.state.sse_broadcaster
    original = broadcaster.publicar

    async def espiar(evento, tipo):
        publicados.append((evento, tipo))
        return await original(evento, tipo)

    # HU-17: 2+ lecturas previas válidas para que la IA pueda calcular
    # tendencia térmica y esta lectura salga como "lectura" (clasificada),
    # no "inferencia_omitida".
    client.post("/api/lecturas", json=_payload(AHORA - timedelta(minutes=10)), headers=auth_header(token_tecnico))
    client.post("/api/lecturas", json=_payload(AHORA - timedelta(minutes=5)), headers=auth_header(token_tecnico))

    broadcaster.publicar = espiar
    try:
        respuesta = client.post(
            "/api/lecturas",
            json=_payload(AHORA - timedelta(minutes=1)),
            headers=auth_header(token_tecnico),
        )
        assert respuesta.status_code == 201
    finally:
        broadcaster.publicar = original

    tipos = [tipo for _, tipo in publicados]
    assert "lectura" in tipos
    assert publicados[0][0]["device_id"] == "ESP32-TS-01"


def test_ingesta_http_de_excursion_critica_publica_alerta(client, token_tecnico, app):
    publicados: list[tuple[dict, str]] = []
    broadcaster = app.state.sse_broadcaster
    original = broadcaster.publicar

    async def espiar(evento, tipo):
        publicados.append((evento, tipo))
        return await original(evento, tipo)

    # HU-17: mismo motivo que en la prueba anterior.
    client.post("/api/lecturas", json=_payload(AHORA - timedelta(minutes=10)), headers=auth_header(token_tecnico))
    client.post("/api/lecturas", json=_payload(AHORA - timedelta(minutes=5)), headers=auth_header(token_tecnico))

    broadcaster.publicar = espiar
    try:
        client.post(
            "/api/lecturas",
            json=_payload(AHORA - timedelta(minutes=1), temperatura_interna=16.0),
            headers=auth_header(token_tecnico),
        )
    finally:
        broadcaster.publicar = original

    tipos = [tipo for _, tipo in publicados]
    assert "lectura" in tipos
    assert "alerta" in tipos
