"""Un login ya bloqueado no puede llenar la bitácora inmutable: quien insiste
contra una cuenta o IP bloqueada deja una fila por ventana, no una por petición."""

from src.domain.value_objects.rol import Rol
from tests.conftest import auth_header

CORREO = "insistente@farmacia.example.org"


async def test_insistir_contra_una_cuenta_bloqueada_deja_una_sola_fila(client, crear_usuario, token_admin):
    await crear_usuario("Insistente", CORREO, "password123", Rol.TECNICO)

    respuestas = [
        client.post("/api/auth/login", data={"username": CORREO, "password": "incorrecta"})
        for _ in range(40)
    ]

    assert respuestas[-1].status_code == 429
    auditoria = client.get(
        "/api/auditoria",
        params={"accion": "UMBRAL_INTENTOS_FALLIDOS_EXCEDIDO"},
        headers=auth_header(token_admin),
    ).json()
    assert len(auditoria) == 1
