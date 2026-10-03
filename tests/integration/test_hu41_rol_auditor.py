"""HU-41 (backlog de 51 HU finales): el rol AUDITOR debe existir en la
matriz RBAC (ADMINISTRADOR/FARMACEUTICO/TECNICO/AUDITOR) con acceso de solo
lectura a trazabilidad/auditoría/reportes, y debe existir un endpoint para
asignar/modificar el rol de un usuario existente tras el alta."""

from tests.conftest import auth_header


def test_auditor_puede_leer_trazabilidad(client, token_auditor):
    response = client.get("/api/trazabilidad", headers=auth_header(token_auditor))
    assert response.status_code == 200


def test_auditor_puede_leer_bitacora_de_auditoria(client, token_auditor):
    response = client.get("/api/auditoria", headers=auth_header(token_auditor))
    assert response.status_code == 200


def test_auditor_no_puede_gestionar_usuarios(client, token_auditor):
    """Solo lectura: AUDITOR no hereda privilegios de ADMINISTRADOR."""
    response = client.get("/api/usuarios", headers=auth_header(token_auditor))
    assert response.status_code == 403


def test_auditor_no_puede_ingestar_lecturas(client, token_auditor):
    """AUDITOR es de solo lectura incluso sobre la ingesta operativa."""
    response = client.post(
        "/api/lecturas",
        json={
            "device_id": "FARM-AUD-01",
            "timestamp": "2026-09-01T12:00:00Z",
            "temperatura_interna": 5.0,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token_auditor),
    )
    assert response.status_code == 403


def test_admin_puede_cambiar_rol_de_usuario(client, token_admin, token_farmaceutico, crear_usuario):
    creado = client.post(
        "/api/usuarios",
        json={
            "nombre": "Cambia Rol",
            "email": "cambia.rol@farmacia.example.org",
            "password": "password123",
            "rol": "tecnico",
        },
        headers=auth_header(token_admin),
    ).json()
    assert creado["rol"] == "tecnico"

    respuesta = client.patch(
        f"/api/usuarios/{creado['id']}/rol",
        json={"rol": "auditor"},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["rol"] == "auditor"


def test_no_admin_no_puede_cambiar_rol(client, token_farmaceutico, token_admin):
    creado = client.post(
        "/api/usuarios",
        json={
            "nombre": "Otro Usuario",
            "email": "otro.usuario@farmacia.example.org",
            "password": "password123",
            "rol": "tecnico",
        },
        headers=auth_header(token_admin),
    ).json()

    respuesta = client.patch(
        f"/api/usuarios/{creado['id']}/rol",
        json={"rol": "auditor"},
        headers=auth_header(token_farmaceutico),
    )
    assert respuesta.status_code == 403


def test_cambiar_rol_de_usuario_inexistente_da_404(client, token_admin):
    respuesta = client.patch(
        "/api/usuarios/00000000-0000-0000-0000-000000000000/rol",
        json={"rol": "auditor"},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 404


def test_cambio_de_rol_queda_auditado_y_encadenado(client, token_admin):
    creado = client.post(
        "/api/usuarios",
        json={
            "nombre": "Trazado Rol",
            "email": "trazado.rol@farmacia.example.org",
            "password": "password123",
            "rol": "tecnico",
        },
        headers=auth_header(token_admin),
    ).json()

    client.patch(
        f"/api/usuarios/{creado['id']}/rol",
        json={"rol": "farmaceutico"},
        headers=auth_header(token_admin),
    )

    auditoria = client.get("/api/auditoria", headers=auth_header(token_admin)).json()
    assert any(entrada["accion"] == "CAMBIAR_ROL_USUARIO" for entrada in auditoria)

    trazabilidad = client.get("/api/trazabilidad", headers=auth_header(token_admin)).json()
    assert any(r["tipo_evento"] == "AUDIT_LOG" for r in trazabilidad)
