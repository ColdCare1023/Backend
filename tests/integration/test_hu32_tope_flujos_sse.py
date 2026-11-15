"""HU-32 esc. 3: tope de flujos SSE simultáneos por cuenta (BE-019).

El tope existía (entonces 3 por cuenta) pero el frontend no podía distinguirlo: el 429
llegaba al EventSource, que no expone el cuerpo ni el código de estado, y la
pestaña quedaba en «Reconectando…» indefinidamente. Ahora:

- `POST /api/auth/sse-ticket` (que el frontend lee con fetch/axios) responde
  429 con `detail: "demasiados_flujos"` cuando la cuenta ya tiene el máximo
  abierto, antes de emitir un ticket que no podría usar;
- `GET /api/sse/lecturas` responde el mismo código si la carrera entre dos
  pestañas lo hace llegar hasta ahí;
- el tope se configura con `SSE_MAX_STREAMS_POR_USUARIO` (por defecto 10, para
  que «todas las instancias» del cliente reciban el evento en el uso real)."""

import pytest
from pydantic import ValidationError

from src.infrastructure.config import Settings, get_settings
from src.infrastructure.security.jwt_handler import JWTHandler
from src.interface.api.sse_router import DETALLE_DEMASIADOS_FLUJOS
from tests.conftest import auth_header


def _usuario(client, token) -> object:
    ticket = client.post("/api/auth/sse-ticket", headers=auth_header(token)).json()["ticket"]
    return ticket, JWTHandler(get_settings()).validar_ticket_sse(ticket).usuario_id


def test_el_codigo_es_estable():
    assert DETALLE_DEMASIADOS_FLUJOS == "demasiados_flujos"


def test_el_ticket_se_niega_con_codigo_estable_al_llegar_al_tope(client, app, token_tecnico):
    _ticket, usuario = _usuario(client, token_tecnico)
    app.state.sse_streams_por_usuario[usuario] = get_settings().sse_max_streams_por_usuario

    respuesta = client.post("/api/auth/sse-ticket", headers=auth_header(token_tecnico))

    assert respuesta.status_code == 429
    assert respuesta.json() == {"detail": "demasiados_flujos"}
    assert int(respuesta.headers["Retry-After"]) > 0


def test_por_debajo_del_tope_el_ticket_se_emite(client, app, token_tecnico):
    _ticket, usuario = _usuario(client, token_tecnico)
    app.state.sse_streams_por_usuario[usuario] = get_settings().sse_max_streams_por_usuario - 1

    respuesta = client.post("/api/auth/sse-ticket", headers=auth_header(token_tecnico))

    assert respuesta.status_code == 200
    assert respuesta.json()["ticket"]


def test_el_stream_responde_el_mismo_codigo(client, app, token_tecnico):
    """Dos pestañas pidieron ticket a la vez y la otra abrió primero."""
    ticket, usuario = _usuario(client, token_tecnico)
    app.state.sse_streams_por_usuario[usuario] = get_settings().sse_max_streams_por_usuario

    respuesta = client.get("/api/sse/lecturas", params={"ticket": ticket})

    assert respuesta.status_code == 429
    assert respuesta.json() == {"detail": "demasiados_flujos"}
    assert int(respuesta.headers["Retry-After"]) > 0


def test_el_tope_lo_fija_la_configuracion(client, app, token_tecnico, monkeypatch):
    _ticket, usuario = _usuario(client, token_tecnico)
    monkeypatch.setattr(get_settings(), "sse_max_streams_por_usuario", 5)
    app.state.sse_streams_por_usuario[usuario] = 3

    assert client.post("/api/auth/sse-ticket", headers=auth_header(token_tecnico)).status_code == 200

    app.state.sse_streams_por_usuario[usuario] = 5
    assert client.post("/api/auth/sse-ticket", headers=auth_header(token_tecnico)).status_code == 429


def test_el_tope_es_por_cuenta(client, app, token_tecnico, token_farmaceutico):
    _ticket, tecnico = _usuario(client, token_tecnico)
    app.state.sse_streams_por_usuario[tecnico] = get_settings().sse_max_streams_por_usuario

    assert client.post("/api/auth/sse-ticket", headers=auth_header(token_farmaceutico)).status_code == 200


def test_sse_max_streams_por_usuario_se_lee_del_entorno(monkeypatch):
    assert Settings().sse_max_streams_por_usuario == 10
    monkeypatch.setenv("SSE_MAX_STREAMS_POR_USUARIO", "6")
    assert Settings().sse_max_streams_por_usuario == 6


def test_por_defecto_cuatro_pestanas_de_una_cuenta_reciben_el_tiempo_real(client, app, token_tecnico, monkeypatch):
    """HU-32 esc. 3: «todas las instancias del cliente» se actualizan. Con el
    tope anterior (3) la cuarta pestaña veía «Demasiadas pestañas»."""
    monkeypatch.delenv("SSE_MAX_STREAMS_POR_USUARIO", raising=False)
    monkeypatch.setattr(get_settings(), "sse_max_streams_por_usuario", Settings().sse_max_streams_por_usuario)
    _ticket, usuario = _usuario(client, token_tecnico)
    app.state.sse_streams_por_usuario[usuario] = 3

    assert client.post("/api/auth/sse-ticket", headers=auth_header(token_tecnico)).status_code == 200


@pytest.mark.parametrize("valor", ["0", "-1", "101"])
def test_un_tope_fuera_de_rango_se_rechaza_al_arrancar(monkeypatch, valor):
    """0 dejaría a todas las cuentas sin tiempo real; un tope desmesurado
    anularía la protección de memoria y conexiones (BE-019)."""
    monkeypatch.setenv("SSE_MAX_STREAMS_POR_USUARIO", valor)
    with pytest.raises(ValidationError):
        Settings()
