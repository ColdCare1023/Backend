from src.domain.value_objects.rol import Rol


async def test_login_exitoso_devuelve_token(client, crear_usuario):
    await crear_usuario("Juan Perez", "juan@farmacia.example.org", "password123", Rol.TECNICO)

    response = client.post(
        "/api/auth/login", data={"username": "juan@farmacia.example.org", "password": "password123"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 0


async def test_login_con_password_incorrecto_falla(client, crear_usuario):
    await crear_usuario("Juan Perez", "juan2@farmacia.example.org", "password123", Rol.TECNICO)

    response = client.post(
        "/api/auth/login", data={"username": "juan2@farmacia.example.org", "password": "incorrecta"}
    )

    assert response.status_code == 401


async def test_login_con_usuario_inexistente_falla(client):
    response = client.post(
        "/api/auth/login", data={"username": "no-existe@farmacia.example.org", "password": "password123"}
    )
    assert response.status_code == 401


async def test_endpoint_protegido_sin_token_falla(client):
    response = client.get("/api/lecturas")
    assert response.status_code == 401
