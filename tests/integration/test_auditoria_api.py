from tests.conftest import auth_header


async def test_crear_usuario_genera_registro_de_auditoria(client, token_admin):
    client.post(
        "/api/usuarios",
        json={
            "nombre": "Usuario Auditado",
            "email": "auditado@farmacia.example.org",
            "password": "password123",
            "rol": "tecnico",
        },
        headers=auth_header(token_admin),
    )

    response = client.get("/api/auditoria", headers=auth_header(token_admin))

    assert response.status_code == 200
    registros = response.json()
    acciones = [r["accion"] for r in registros]
    assert "CREAR_USUARIO" in acciones


async def test_no_administrador_no_puede_ver_auditoria(client, token_farmaceutico):
    response = client.get("/api/auditoria", headers=auth_header(token_farmaceutico))
    assert response.status_code == 403
