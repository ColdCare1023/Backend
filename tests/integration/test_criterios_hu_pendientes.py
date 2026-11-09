"""HU-27, HU-30, HU-46, HU-47 y HU-49: criterios verificados por API."""

import uuid
from datetime import date, datetime, timedelta, timezone

from tests.conftest import auth_header

DEVICE_ID = "ESP32-PEND-01"
HOY = datetime.now(tz=timezone.utc).date()


def _alta(client, token) -> None:
    client.post(
        "/api/dispositivos",
        json={"device_id": DEVICE_ID, "sensores_habilitados": ["ds18b20"]},
        headers=auth_header(token),
    )


def test_hu47_intento_de_modificar_la_inferencia_queda_auditado(client, token_auditor, token_admin):
    respuesta = client.patch(
        f"/api/ia/inferencia/{uuid.uuid4()}", json={"nivel": "normal"}, headers=auth_header(token_auditor)
    )

    assert respuesta.status_code == 405
    assert respuesta.headers["allow"] == "GET"
    registros = client.get(
        "/api/auditoria", params={"accion": "INTENTO_MODIFICAR_INFERENCIA"}, headers=auth_header(token_admin)
    ).json()
    assert len(registros) == 1


def test_hu46_no_se_activa_si_el_artefacto_desplegado_es_otro(client, token_admin):
    client.post(
        "/api/ia/modelo/versiones",
        json={
            "version": "9.0.0-otro", "model_hash": "c" * 64, "feature_schema_version": "1",
            "dataset_version": "d", "scikit_learn_version": "1.5.0", "python_version": "3.12.0",
            "trained_at": "2026-08-01T00:00:00Z", "horizonte_min": 30,
            "metricas": {
                "f1_macro": 0.88, "incidentes_evaluables": 12, "sensibilidad_incidente": 0.92,
                "ic95_inferior_sensibilidad": 0.62, "falsas_alarmas_por_dia_sensor": 1.1,
                "mediana_anticipacion_min": 9.0, "ap_random_forest": 0.71, "ap_mejor_baseline": 0.55,
            },
            "particiones": {"entrenamiento": "a", "prueba": "b", "separacion_min": 30},
        },
        headers=auth_header(token_admin),
    )

    respuesta = client.post("/api/ia/modelo/versiones/9.0.0-otro/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 409
    assert "artefacto" in respuesta.json()["detail"]


def test_hu30_guarda_la_fecha_del_certificado_y_el_margen(client, token_admin):
    _alta(client, token_admin)
    vence = HOY + timedelta(days=200)

    respuesta = client.patch(
        f"/api/dispositivos/{DEVICE_ID}/calibracion",
        json={"fecha_calibracion": HOY.isoformat(), "numero_certificado": "C-1",
              "fecha_vencimiento": vence.isoformat(), "margen_aviso_dias": 210},
        headers=auth_header(token_admin),
    )

    assert respuesta.status_code == 200
    assert date.fromisoformat(respuesta.json()["fecha_proxima_calibracion"]) == vence
    assert respuesta.json()["margen_aviso_calibracion_dias"] == 210
    estado = client.get("/api/dispositivos/calibracion/estado", headers=auth_header(token_admin)).json()
    assert [d["id"] for d in estado["proximos_a_vencer"]] == [DEVICE_ID]


def test_hu30_sin_vigencia_no_se_impone_un_periodo(client, token_admin):
    _alta(client, token_admin)

    respuesta = client.patch(
        f"/api/dispositivos/{DEVICE_ID}/calibracion",
        json={"fecha_calibracion": HOY.isoformat(), "numero_certificado": "C-2"},
        headers=auth_header(token_admin),
    )

    assert respuesta.status_code == 422


def test_hu49_el_historial_registra_fecha_y_observaciones_de_instalacion(client, token_admin):
    _alta(client, token_admin)

    client.patch(
        f"/api/dispositivos/{DEVICE_ID}/instalacion",
        json={"ubicacion": "Estante 1", "fecha_instalacion": "2026-08-01", "observaciones": "Con cinta"},
        headers=auth_header(token_admin),
    )
    historial = client.get(
        f"/api/dispositivos/{DEVICE_ID}/historial-configuracion", headers=auth_header(token_admin)
    ).json()

    campos = {h["campo"]: h["valor_nuevo"] for h in historial}
    assert campos["fecha_instalacion"] == "2026-08-01"
    assert campos["observaciones_instalacion"] == "Con cinta"
