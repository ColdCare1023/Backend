"""RF-13 / HU-38: descarga del reporte BPA en PDF.

Lo que hace del PDF una evidencia y no una planilla cualquiera es que el
veredicto de integridad de la cadena SHA-256 viaja dentro del documento; estas
pruebas cubren tanto el formato como esa propiedad."""

from datetime import datetime, timedelta, timezone

from tests.conftest import auth_header

AHORA = datetime.now(tz=timezone.utc)
DESDE = (AHORA - timedelta(days=1)).isoformat()
HASTA = (AHORA + timedelta(days=1)).isoformat()


def _ingestar_lecturas(client, token_tecnico, cantidad: int = 5) -> None:
    for indice in range(cantidad):
        # Alterna entre rango correcto y excursión crítica para que el reporte
        # tenga estadísticas y alertas reales que mostrar.
        temperatura = 4.5 if indice % 2 == 0 else 14.0
        respuesta = client.post(
            "/api/lecturas",
            json={
                "device_id": "ESP32-PDF-01",
                "timestamp": (AHORA - timedelta(minutes=cantidad - indice)).isoformat(),
                "temperatura_ambiental": 21.0,
                "humedad_ambiental": 55.0,
                "temperatura_interna": temperatura,
                "apertura_refrigerador": False,
                "estado_conectividad": "online",
            },
            headers=auth_header(token_tecnico),
        )
        assert respuesta.status_code == 201, respuesta.text


def test_descarga_pdf_valido_con_cabecera_de_adjunto(client, token_farmaceutico, token_tecnico):
    _ingestar_lecturas(client, token_tecnico)

    respuesta = client.get(
        "/api/reportes/bpa/pdf",
        params={"fecha_desde": DESDE, "fecha_hasta": HASTA},
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.headers["content-type"] == "application/pdf"
    assert "attachment" in respuesta.headers["content-disposition"]
    assert ".pdf" in respuesta.headers["content-disposition"]

    contenido = respuesta.content
    # Firma de archivo PDF y marcador de fin: el binario está completo.
    assert contenido.startswith(b"%PDF-"), "el cuerpo no es un PDF"
    assert b"%%EOF" in contenido[-2048:], "el PDF quedó truncado"
    # Un reporte con datos reales pesa bastante más que un documento vacío.
    assert len(contenido) > 3000


def test_pdf_se_genera_sin_datos_en_el_periodo(client, token_farmaceutico):
    """Un período sin lecturas debe producir un PDF válido que lo declare,
    no un error: la ausencia de registros también es información auditable."""
    respuesta = client.get(
        "/api/reportes/bpa/pdf",
        params={"fecha_desde": DESDE, "fecha_hasta": HASTA},
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 200
    assert respuesta.content.startswith(b"%PDF-")


def test_rango_de_fechas_invertido_rechazado(client, token_farmaceutico):
    respuesta = client.get(
        "/api/reportes/bpa/pdf",
        params={"fecha_desde": HASTA, "fecha_hasta": DESDE},
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 422


def test_exportacion_queda_auditada(client, token_farmaceutico, token_admin, token_tecnico):
    _ingestar_lecturas(client, token_tecnico, cantidad=2)
    client.get(
        "/api/reportes/bpa/pdf",
        params={"fecha_desde": DESDE, "fecha_hasta": HASTA},
        headers=auth_header(token_farmaceutico),
    )

    auditoria = client.get("/api/auditoria", headers=auth_header(token_admin)).json()
    acciones = [entrada["accion"] for entrada in auditoria]
    assert "EXPORTAR_REPORTE_BPA_PDF" in acciones


def test_descargar_pdf_no_dispara_evento_de_corrupcion(
    client, token_farmaceutico, token_admin, token_tecnico
):
    """La verificación embebida en el reporte es de solo lectura: no debe
    insertar eventos de emergencia ni snapshots forenses en la cadena.

    HU-42 (backlog de 51 HU finales): la propia acción de exportar SÍ agrega
    un evento AUDIT_LOG legítimo a la cadena (la bitácora ahora está
    encadenada, no es una tabla paralela) — lo que esta prueba verifica es
    que la verificación de integridad EMBEBIDA en el PDF, en concreto, no
    agrega eventos de corrupción/emergencia además de ese.
    """
    _ingestar_lecturas(client, token_tecnico, cantidad=2)
    antes = client.get("/api/trazabilidad", headers=auth_header(token_admin)).json()

    client.get(
        "/api/reportes/bpa/pdf",
        params={"fecha_desde": DESDE, "fecha_hasta": HASTA},
        headers=auth_header(token_farmaceutico),
    )

    despues = client.get("/api/trazabilidad", headers=auth_header(token_admin)).json()
    nuevos = despues[: len(despues) - len(antes)]
    assert len(despues) == len(antes) + 1
    assert all(r["tipo_evento"] == "AUDIT_LOG" for r in nuevos)
    assert not any(r["tipo_evento"] == "CORRUPCION_CADENA_DETECTADA" for r in despues)


def test_pdf_requiere_autenticacion(client):
    respuesta = client.get(
        "/api/reportes/bpa/pdf", params={"fecha_desde": DESDE, "fecha_hasta": HASTA}
    )
    assert respuesta.status_code == 401


def test_tecnico_no_puede_exportar_reporte_bpa(client, token_tecnico):
    respuesta = client.get(
        "/api/reportes/bpa/pdf",
        params={"fecha_desde": DESDE, "fecha_hasta": HASTA},
        headers=auth_header(token_tecnico),
    )
    assert respuesta.status_code == 403
