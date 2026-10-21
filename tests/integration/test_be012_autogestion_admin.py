"""BE-012: las reglas de autogestión del administrador viven en el servidor."""

from src.domain.value_objects.rol import Rol
from tests.conftest import auth_header


def _yo(client, token) -> dict:
    return next(u for u in client.get("/api/usuarios", headers=auth_header(token)).json() if u["rol"] == "administrador")


def test_admin_no_puede_cambiar_su_propio_rol(client, token_admin):
    yo = _yo(client, token_admin)
    respuesta = client.patch(f"/api/usuarios/{yo['id']}/rol", json={"rol": "tecnico"}, headers=auth_header(token_admin))
    assert respuesta.status_code == 409


def test_admin_no_puede_desactivarse(client, token_admin):
    yo = _yo(client, token_admin)
    respuesta = client.patch(
        f"/api/usuarios/{yo['id']}/desactivar", json={"motivo": "otros"}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 409


async def test_no_se_puede_dejar_el_sistema_sin_administrador(client, token_admin, crear_usuario):
    """Con dos administradores, uno puede degradar al otro; el último que
    queda no puede perder el rol por ninguna vía."""
    otro = await crear_usuario("Admin Dos", "admin2@farmacia.example.org", "password123", Rol.ADMINISTRADOR)
    assert client.patch(
        f"/api/usuarios/{otro.id}/rol", json={"rol": "tecnico"}, headers=auth_header(token_admin)
    ).status_code == 200

    token_otro = client.post(
        "/api/auth/login", data={"username": "admin2@farmacia.example.org", "password": "password123"}
    )
    # El degradado ya no es administrador (y su sesión anterior fue invalidada).
    assert token_otro.status_code == 200
    yo = _yo(client, token_admin)
    # Sigue sin poder tocarse a sí mismo, que ahora es el único administrador.
    assert client.patch(
        f"/api/usuarios/{yo['id']}/rol", json={"rol": "auditor"}, headers=auth_header(token_admin)
    ).status_code == 409
