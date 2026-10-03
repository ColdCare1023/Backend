from tests.conftest import auth_header


async def test_admin_puede_crear_usuario(client, token_admin):
    response = client.post(
        "/api/usuarios",
        json={
            "nombre": "Nuevo Tecnico",
            "email": "nuevo.tecnico@farmacia.example.org",
            "password": "password123",
            "rol": "tecnico",
        },
        headers=auth_header(token_admin),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "nuevo.tecnico@farmacia.example.org"
    assert body["rol"] == "tecnico"


async def test_admin_no_puede_crear_usuario_duplicado(client, token_admin):
    payload = {
        "nombre": "Duplicado",
        "email": "duplicado@farmacia.example.org",
        "password": "password123",
        "rol": "tecnico",
    }
    primero = client.post("/api/usuarios", json=payload, headers=auth_header(token_admin))
    segundo = client.post("/api/usuarios", json=payload, headers=auth_header(token_admin))

    assert primero.status_code == 201
    assert segundo.status_code == 409


async def test_tecnico_no_puede_crear_usuarios(client, token_tecnico):
    response = client.post(
        "/api/usuarios",
        json={
            "nombre": "Intento",
            "email": "intento@farmacia.example.org",
            "password": "password123",
            "rol": "tecnico",
        },
        headers=auth_header(token_tecnico),
    )

    assert response.status_code == 403


async def test_admin_puede_listar_usuarios(client, token_admin):
    response = client.get("/api/usuarios", headers=auth_header(token_admin))
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) >= 1
