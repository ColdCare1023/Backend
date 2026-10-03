from tests.conftest import auth_header


async def test_admin_desactiva_usuario(client, token_admin, crear_usuario):
    from src.domain.value_objects.rol import Rol

    usuario = await crear_usuario("Saliente", "saliente@farmacia.example.org", "password123", Rol.TECNICO)

    response = client.patch(
        f"/api/usuarios/{usuario.id}/desactivar",
        json={"motivo": "renuncia"},
        headers=auth_header(token_admin),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_active"] is False
    assert body["motivo_desactivacion"] == "renuncia"
    assert body["desactivado_en"] is not None


async def test_usuario_desactivado_no_puede_hacer_login(client, token_admin, crear_usuario):
    from src.domain.value_objects.rol import Rol

    usuario = await crear_usuario("Bloqueado", "bloqueado@farmacia.example.org", "password123", Rol.TECNICO)
    client.patch(
        f"/api/usuarios/{usuario.id}/desactivar",
        json={"motivo": "despido"},
        headers=auth_header(token_admin),
    )

    login = client.post(
        "/api/auth/login", data={"username": "bloqueado@farmacia.example.org", "password": "password123"}
    )
    assert login.status_code == 401


async def test_token_ya_emitido_deja_de_valer_tras_desactivar(client, token_admin, crear_usuario):
    from src.domain.value_objects.rol import Rol

    usuario = await crear_usuario("Sesion Activa", "sesion.activa@farmacia.example.org", "password123", Rol.TECNICO)
    login = client.post(
        "/api/auth/login",
        data={"username": "sesion.activa@farmacia.example.org", "password": "password123"},
    )
    token = login.json()["access_token"]
    # Nueva sesión debe aceptar privacidad para no interferir con el escenario probado.
    client.post("/api/auth/privacidad/aceptar", headers=auth_header(token))

    client.patch(
        f"/api/usuarios/{usuario.id}/desactivar",
        json={"motivo": "otros"},
        headers=auth_header(token_admin),
    )

    respuesta = client.get("/api/usuarios", headers=auth_header(token))
    assert respuesta.status_code == 401


async def test_desactivar_con_motivo_invalido_rechaza(client, token_admin, crear_usuario):
    from src.domain.value_objects.rol import Rol

    usuario = await crear_usuario("Motivo Malo", "motivo.malo@farmacia.example.org", "password123", Rol.TECNICO)

    response = client.patch(
        f"/api/usuarios/{usuario.id}/desactivar",
        json={"motivo": "porque_si"},
        headers=auth_header(token_admin),
    )
    assert response.status_code == 409


async def test_desactivar_usuario_inexistente_404(client, token_admin):
    import uuid

    response = client.patch(
        f"/api/usuarios/{uuid.uuid4()}/desactivar",
        json={"motivo": "otros"},
        headers=auth_header(token_admin),
    )
    assert response.status_code == 404


async def test_desactivar_dos_veces_es_conflicto(client, token_admin, crear_usuario):
    from src.domain.value_objects.rol import Rol

    usuario = await crear_usuario("Doble Baja", "doble.baja@farmacia.example.org", "password123", Rol.TECNICO)
    primero = client.patch(
        f"/api/usuarios/{usuario.id}/desactivar",
        json={"motivo": "jubilacion"},
        headers=auth_header(token_admin),
    )
    assert primero.status_code == 200

    segundo = client.patch(
        f"/api/usuarios/{usuario.id}/desactivar",
        json={"motivo": "jubilacion"},
        headers=auth_header(token_admin),
    )
    assert segundo.status_code == 409


async def test_tecnico_no_puede_desactivar_usuarios(client, token_tecnico, crear_usuario):
    from src.domain.value_objects.rol import Rol

    usuario = await crear_usuario("Protegido", "protegido@farmacia.example.org", "password123", Rol.TECNICO)

    response = client.patch(
        f"/api/usuarios/{usuario.id}/desactivar",
        json={"motivo": "otros"},
        headers=auth_header(token_tecnico),
    )
    assert response.status_code == 403


async def test_desactivacion_queda_en_trazabilidad(client, token_admin, token_tecnico, crear_usuario):
    from src.domain.value_objects.rol import Rol

    usuario = await crear_usuario("Auditado Baja", "auditado.baja@farmacia.example.org", "password123", Rol.TECNICO)
    client.patch(
        f"/api/usuarios/{usuario.id}/desactivar",
        json={"motivo": "renuncia"},
        headers=auth_header(token_admin),
    )

    # El endpoint de trazabilidad exige rol TECNICO/FARMACEUTICO (ADMINISTRADOR
    # no está en esa lista), así que se lee con un técnico.
    trazabilidad = client.get(
        "/api/trazabilidad",
        params={"tipo_evento": "DESACTIVACION_USUARIO"},
        headers=auth_header(token_tecnico),
    )
    assert trazabilidad.status_code == 200
    assert len(trazabilidad.json()) == 1
    assert trazabilidad.json()[0]["payload"]["usuario_desactivado_id"] == str(usuario.id)
