"""HU-37: verificación de integridad de telemetría acotada a un dispositivo
y un periodo — a diferencia de HU-26 (GET /verificar), que recorre la
cadena global completa sin acotar por dispositivo ni rango."""

from datetime import datetime, timedelta, timezone

from tests.conftest import auth_header

DEVICE_ID = "FARM-HU37-01"
OTRO_DEVICE_ID = "FARM-HU37-02"


def _ingestar(client, token, device_id, minutos_atras) -> None:
    ts = (datetime.now(tz=timezone.utc) - timedelta(minutes=minutos_atras)).isoformat()
    respuesta = client.post(
        "/api/lecturas",
        json={
            "device_id": device_id,
            "timestamp": ts,
            "temperatura_interna": 5.0,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token),
    )
    assert respuesta.status_code == 201


def test_endpoint_acepta_device_id_y_rango(client, token_tecnico):
    """El endpoint global /verificar NO acepta estos parámetros; este sí."""
    _ingestar(client, token_tecnico, DEVICE_ID, 10)

    desde = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    hasta = datetime.now(tz=timezone.utc).isoformat()

    respuesta = client.get(
        "/api/trazabilidad/verificar-dispositivo",
        params={"device_id": DEVICE_ID, "desde": desde, "hasta": hasta},
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["device_id"] == DEVICE_ID
    assert cuerpo["integra"] is True
    assert len(cuerpo["registros_del_dispositivo"]) >= 1


def test_solo_filtra_las_lecturas_del_dispositivo_consultado(client, token_tecnico):
    """HU-25: cada dispositivo tiene su propia cadena (chain_id=device_id) —
    los eventos de OTRO_DEVICE_ID viven en una cadena distinta y no forman
    parte de la cadena de DEVICE_ID en absoluto (a diferencia del diseño
    anterior de cadena global, donde compartían la misma secuencia y solo se
    filtraban al presentar el resultado)."""
    _ingestar(client, token_tecnico, DEVICE_ID, 10)
    _ingestar(client, token_tecnico, OTRO_DEVICE_ID, 8)
    _ingestar(client, token_tecnico, DEVICE_ID, 5)

    desde = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    hasta = datetime.now(tz=timezone.utc).isoformat()

    respuesta = client.get(
        "/api/trazabilidad/verificar-dispositivo",
        params={"device_id": DEVICE_ID, "desde": desde, "hasta": hasta},
        headers=auth_header(token_tecnico),
    )
    cuerpo = respuesta.json()
    # Solo los 2 bloques de la cadena de DEVICE_ID: el de OTRO_DEVICE_ID vive
    # en su propia cadena y nunca la comparte.
    assert cuerpo["total_bloques_verificados"] == 2
    assert len(cuerpo["registros_del_dispositivo"]) == 2
    ids_devueltos = {r["id"] for r in cuerpo["registros_del_dispositivo"]}
    assert len(ids_devueltos) == len(cuerpo["registros_del_dispositivo"])


def test_rango_invertido_se_rechaza(client, token_tecnico):
    desde = datetime.now(tz=timezone.utc).isoformat()
    hasta = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()

    respuesta = client.get(
        "/api/trazabilidad/verificar-dispositivo",
        params={"device_id": DEVICE_ID, "desde": desde, "hasta": hasta},
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 422


def test_rango_sin_datos_es_integro_y_vacio(client, token_tecnico):
    desde = (datetime.now(tz=timezone.utc) - timedelta(days=10)).isoformat()
    hasta = (datetime.now(tz=timezone.utc) - timedelta(days=9)).isoformat()

    respuesta = client.get(
        "/api/trazabilidad/verificar-dispositivo",
        params={"device_id": DEVICE_ID, "desde": desde, "hasta": hasta},
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["integra"] is True
    assert cuerpo["registros_del_dispositivo"] == []


def test_verificacion_queda_registrada_como_evento_append_only(client, token_tecnico, token_admin):
    _ingestar(client, token_tecnico, DEVICE_ID, 10)
    desde = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    hasta = datetime.now(tz=timezone.utc).isoformat()

    client.get(
        "/api/trazabilidad/verificar-dispositivo",
        params={"device_id": DEVICE_ID, "desde": desde, "hasta": hasta},
        headers=auth_header(token_tecnico),
    )

    trazabilidad = client.get(
        "/api/trazabilidad",
        params={"tipo_evento": "VERIFICACION_INTEGRIDAD"},
        headers=auth_header(token_admin),
    ).json()
    assert len(trazabilidad) == 1
    assert trazabilidad[0]["device_id"] == DEVICE_ID
