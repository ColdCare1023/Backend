"""HU-30: registro de calibración de sensores con trazabilidad SHA-256.

Un registro térmico solo es evidencia válida ante inspección si el instrumento
que lo produjo tenía certificado vigente; estas pruebas cubren ese vínculo."""

from datetime import datetime, timedelta, timezone

import pytest_asyncio

from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from tests.conftest import auth_header

HOY = datetime.now(tz=timezone.utc).date()


@pytest_asyncio.fixture
async def device_registrado(db_session_factory):
    async with db_session_factory() as session:
        repo = SQLAlchemyDeviceRepository(session)
        await repo.obtener_o_crear("ESP32-CALIB-01")
        await session.commit()
    return "ESP32-CALIB-01"


def _payload(**overrides) -> dict:
    base = {
        "fecha_calibracion": HOY.isoformat(),
        "numero_certificado": "CERT-INACAL-2026-0091",
        "observaciones": "Calibración anual con patrón trazable a INACAL.",
        "meses_vigencia": 12,
    }
    return {**base, **overrides}


def test_registrar_calibracion_persiste_y_calcula_vencimiento(
    client, token_farmaceutico, device_registrado
):
    respuesta = client.patch(
        f"/api/dispositivos/{device_registrado}/calibracion",
        json=_payload(),
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["numero_certificado_calibracion"] == "CERT-INACAL-2026-0091"
    assert cuerpo["fecha_ultima_calibracion"] == HOY.isoformat()
    # 12 meses de vigencia => mismo día del año siguiente.
    assert cuerpo["fecha_proxima_calibracion"] == HOY.replace(year=HOY.year + 1).isoformat()


def test_calibracion_queda_anclada_en_la_cadena_de_hash(
    client, token_farmaceutico, token_admin, device_registrado
):
    client.patch(
        f"/api/dispositivos/{device_registrado}/calibracion",
        json=_payload(),
        headers=auth_header(token_farmaceutico),
    )
    registros = client.get("/api/trazabilidad", headers=auth_header(token_admin)).json()
    calibraciones = [r for r in registros if r["tipo_evento"] == "CALIBRACION_SENSORES"]
    assert len(calibraciones) == 1
    assert calibraciones[0]["payload"]["numero_certificado"] == "CERT-INACAL-2026-0091"
    assert calibraciones[0]["device_id"] == device_registrado

    verificacion = client.get("/api/trazabilidad/verificar", headers=auth_header(token_admin))
    assert verificacion.json()["integra"] is True


def test_fecha_de_calibracion_futura_rechazada(client, token_farmaceutico, device_registrado):
    manana = (HOY + timedelta(days=1)).isoformat()
    respuesta = client.patch(
        f"/api/dispositivos/{device_registrado}/calibracion",
        json=_payload(fecha_calibracion=manana),
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 422


def test_dispositivo_inexistente_devuelve_404(client, token_farmaceutico):
    respuesta = client.patch(
        "/api/dispositivos/NO-EXISTE/calibracion",
        json=_payload(),
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 404


@pytest_asyncio.fixture
async def tres_devices(db_session_factory):
    async with db_session_factory() as session:
        repo = SQLAlchemyDeviceRepository(session)
        for device_id in ("ESP32-VENCIDO", "ESP32-PROXIMO", "ESP32-VIGENTE"):
            await repo.obtener_o_crear(device_id)
        await session.commit()


def test_estado_calibracion_clasifica_vencidos_y_proximos(
    client, token_farmaceutico, tres_devices
):
    cabecera = auth_header(token_farmaceutico)
    # Con 12 meses de vigencia, la fecha de calibración determina el vencimiento:
    #   hace 400 días  -> venció hace ~35 días
    #   hace 355 días  -> vence en ~10 días (dentro del preaviso de 30)
    #   hoy            -> vence en 12 meses
    for device_id, dias_atras in (
        ("ESP32-VENCIDO", 400),
        ("ESP32-PROXIMO", 355),
        ("ESP32-VIGENTE", 0),
    ):
        respuesta = client.patch(
            f"/api/dispositivos/{device_id}/calibracion",
            json=_payload(
                fecha_calibracion=(HOY - timedelta(days=dias_atras)).isoformat(),
                numero_certificado=f"CERT-{device_id}",
            ),
            headers=cabecera,
        )
        assert respuesta.status_code == 200, respuesta.text

    estado = client.get("/api/dispositivos/calibracion/estado", headers=cabecera).json()
    vencidos = {d["id"] for d in estado["vencidos"]}
    proximos = {d["id"] for d in estado["proximos_a_vencer"]}

    assert "ESP32-VENCIDO" in vencidos
    assert "ESP32-PROXIMO" in proximos
    # El vigente no aparece en ninguna de las dos listas.
    assert "ESP32-VIGENTE" not in vencidos
    assert "ESP32-VIGENTE" not in proximos


def test_tecnico_no_puede_registrar_calibracion(client, token_tecnico, device_registrado):
    respuesta = client.patch(
        f"/api/dispositivos/{device_registrado}/calibracion",
        json=_payload(),
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 403


def test_calibracion_requiere_autenticacion(client, device_registrado):
    respuesta = client.patch(
        f"/api/dispositivos/{device_registrado}/calibracion", json=_payload()
    )
    assert respuesta.status_code == 401
