"""Fase 3 de pulido del backlog de 51 HU finales: HU-15 (estado por sensor),
HU-36 (rango invertido server-side), HU-43 (alta explícita), HU-49
(historial de configuración), HU-50 (filtros de auditoría), HU-51
(metadatos de instalación)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.infrastructure.mqtt.payload_schema import LecturaPayload
from tests.conftest import auth_header

DEVICE_ID = "FARM-FASE3-01"


# ── HU-15: estado explícito por sensor ────────────────────────────────────


def test_valor_null_con_estado_ok_se_rechaza():
    with pytest.raises(ValidationError):
        LecturaPayload(
            device_id=DEVICE_ID,
            timestamp=datetime.now(tz=timezone.utc),
            temperatura_interna=None,
            estado_temperatura_interna="ok",
        )


def test_valor_presente_con_estado_de_falla_se_rechaza():
    with pytest.raises(ValidationError):
        LecturaPayload(
            device_id=DEVICE_ID,
            timestamp=datetime.now(tz=timezone.utc),
            temperatura_interna=5.0,
            estado_temperatura_interna="sensor_error",
        )


def test_valor_null_con_estado_de_falla_se_acepta():
    payload = LecturaPayload(
        device_id=DEVICE_ID,
        timestamp=datetime.now(tz=timezone.utc),
        temperatura_interna=None,
        estado_temperatura_interna="sensor_error",
    )
    assert payload.temperatura_interna is None
    assert payload.estado_temperatura_interna == "sensor_error"


def test_sin_estado_declarado_sigue_funcionando_por_compatibilidad():
    payload = LecturaPayload(
        device_id=DEVICE_ID, timestamp=datetime.now(tz=timezone.utc), temperatura_interna=None
    )
    assert payload.estado_temperatura_interna is None


# ── HU-36: rango invertido en el historial, validado server-side ─────────


def test_historial_rango_invertido_se_rechaza_en_el_backend(client, token_tecnico):
    respuesta = client.get(
        "/api/lecturas",
        params={
            "desde": "2026-09-02T00:00:00Z",
            "hasta": "2026-09-01T00:00:00Z",
        },
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 422


# ── HU-43: alta explícita ─────────────────────────────────────────────────


def test_alta_explicita_registra_metadatos(client, token_admin):
    respuesta = client.post(
        "/api/dispositivos",
        json={
            "device_id": DEVICE_ID,
            "nombre": "Refrigerador mostrador",
            "ubicacion": "Farmacia Central, pasillo 2",
            "sensores_habilitados": ["ds18b20", "sht31"],
        },
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["id"] == DEVICE_ID
    assert cuerpo["activo"] is True
    assert set(cuerpo["sensores_habilitados"]) == {"ds18b20", "sht31"}


def test_alta_duplicada_se_rechaza(client, token_admin):
    client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE_ID, "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )
    respuesta = client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE_ID, "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 409


def test_alta_reutilizando_id_de_dispositivo_dado_de_baja_se_rechaza(client, token_admin):
    client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE_ID, "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )
    client.post(
        f"/api/dispositivos/{DEVICE_ID}/baja",
        json={"motivo": "fin_de_servicio"},
        headers=auth_header(token_admin),
    )

    respuesta = client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE_ID, "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 409


# ── HU-51: metadatos de instalación + historial de ubicación ─────────────


def test_actualizar_instalacion_registra_metadatos(client, token_admin):
    client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE_ID, "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )

    respuesta = client.patch(
        f"/api/dispositivos/{DEVICE_ID}/instalacion",
        json={
            "ubicacion": "Refrigerador 1, estante inferior",
            "fecha_instalacion": "2026-08-01",
            "observaciones": "Sensor fijado con cinta térmica junto al termómetro de referencia.",
        },
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["ubicacion"] == "Refrigerador 1, estante inferior"
    assert cuerpo["fecha_instalacion"] == "2026-08-01"


# ── HU-53/HU-54: responsable del dispositivo (destinatario de avisos) ────


def test_actualizar_responsable_persiste_contacto_y_queda_en_el_historial(client, token_admin):
    client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE_ID, "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )

    respuesta = client.patch(
        f"/api/dispositivos/{DEVICE_ID}/responsable",
        json={
            "nombre": "Q.F. Ana Torres",
            "email": "ana.torres@farmacia.example",
            "telefono": "+51999111222",
        },
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["responsable_nombre"] == "Q.F. Ana Torres"
    assert cuerpo["responsable_email"] == "ana.torres@farmacia.example"
    assert cuerpo["responsable_telefono"] == "+51999111222"

    historial = client.get(
        f"/api/dispositivos/{DEVICE_ID}/historial-configuracion", headers=auth_header(token_admin)
    ).json()
    assert any(h["campo"] == "responsable_contacto" for h in historial)


def test_actualizar_responsable_de_dispositivo_inexistente_da_404(client, token_admin):
    respuesta = client.patch(
        "/api/dispositivos/NO-EXISTE/responsable",
        json={"nombre": "X", "email": None, "telefono": None},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 404


def test_cambiar_ubicacion_dos_veces_genera_historial_sin_perder_la_anterior(client, token_admin):
    client.post(
        "/api/dispositivos",
        json={
            "device_id": DEVICE_ID,
            "ubicacion": "Ubicación original",
            "sensores_habilitados": ["ds18b20"],
        },
        headers=auth_header(token_admin),
    )
    client.patch(
        f"/api/dispositivos/{DEVICE_ID}/instalacion",
        json={"ubicacion": "Ubicación nueva"},
        headers=auth_header(token_admin),
    )

    historial = client.get(
        f"/api/dispositivos/{DEVICE_ID}/historial-configuracion", headers=auth_header(token_admin)
    ).json()
    cambios_ubicacion = [h for h in historial if h["campo"] == "ubicacion"]
    assert len(cambios_ubicacion) == 1
    assert cambios_ubicacion[0]["valor_anterior"] == "Ubicación original"
    assert cambios_ubicacion[0]["valor_nuevo"] == "Ubicación nueva"


# ── HU-49: historial también cubre calibración ────────────────────────────


def test_calibracion_tambien_queda_en_el_historial_de_configuracion(
    client, token_admin, token_farmaceutico
):
    client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE_ID, "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )
    client.patch(
        f"/api/dispositivos/{DEVICE_ID}/calibracion",
        json={
            "fecha_calibracion": "2026-08-01",
            "numero_certificado": "CERT-001",
            "meses_vigencia": 12,
        },
        headers=auth_header(token_farmaceutico),
    )

    historial = client.get(
        f"/api/dispositivos/{DEVICE_ID}/historial-configuracion", headers=auth_header(token_admin)
    ).json()
    assert any(h["campo"] == "fecha_ultima_calibracion" for h in historial)


# ── HU-50: filtros del panel de auditoría ─────────────────────────────────


def test_auditoria_admite_filtro_por_accion(client, token_admin):
    client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE_ID, "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token_admin),
    )

    respuesta = client.get(
        "/api/auditoria", params={"accion": "ALTA_DISPOSITIVO"}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert len(cuerpo) >= 1
    assert all(entrada["accion"] == "ALTA_DISPOSITIVO" for entrada in cuerpo)


async def test_umbral_de_intentos_fallidos_genera_evento_administrativo(client, crear_usuario, token_admin):
    from src.domain.value_objects.rol import Rol

    await crear_usuario("Bloqueo Test", "bloqueo@farmacia.example.org", "password123", Rol.TECNICO)

    for _ in range(10):
        client.post(
            "/api/auth/login",
            data={"username": "bloqueo@farmacia.example.org", "password": "incorrecta"},
        )

    auditoria = client.get("/api/auditoria", headers=auth_header(token_admin)).json()
    acciones = [entrada["accion"] for entrada in auditoria]
    assert "UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO" in acciones
