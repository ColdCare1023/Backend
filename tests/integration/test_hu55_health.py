"""HU-55 («HU-56» en la numeración antigua): /health (liveness para Railway) y /health/detalle (operador)."""

from tests.conftest import auth_header


def test_health_responde_sin_autenticacion(client):
    respuesta = client.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"


def test_health_detalle_exige_administrador(client, token_tecnico):
    assert client.get("/health/detalle").status_code == 401
    assert client.get("/health/detalle", headers=auth_header(token_tecnico)).status_code == 403


def test_health_detalle_informa_bd_y_mqtt(client, token_admin):
    respuesta = client.get("/health/detalle", headers=auth_header(token_admin))
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["db"] == "ok"
    assert cuerpo["status"] == "ok"
    # En pruebas la ingesta MQTT no arranca (ENVIRONMENT=test).
    assert cuerpo["mqtt"]["habilitado"] is False
    assert cuerpo["mqtt"]["conectado"] is False
    # Sin BROKER_API_URL no se comprueba la API del broker al arrancar.
    assert cuerpo["broker_api"] == "sin_configurar"


def test_health_detalle_informa_la_cabeza_de_migraciones_del_codigo(client, token_admin):
    """HU-55 esc. 1: con `alembic_head` la evidencia de despliegue comprueba que
    la base está en la última migración del código, no solo en alguna."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parents[2]
    config = Config(str(raiz / "alembic.ini"))
    config.set_main_option("script_location", str(raiz / "alembic"))
    esperada = ScriptDirectory.from_config(config).get_current_head()

    cuerpo = client.get("/health/detalle", headers=auth_header(token_admin)).json()

    assert esperada and cuerpo["alembic_head"] == esperada
    assert cuerpo["alembic_head"].startswith("0042")


def test_health_detalle_degradado_si_la_api_del_broker_fallo(client, token_admin, app):
    app.state.broker_api = False
    try:
        cuerpo = client.get("/health/detalle", headers=auth_header(token_admin)).json()
    finally:
        app.state.broker_api = None

    assert cuerpo["broker_api"] == "error"
    assert cuerpo["status"] == "degradado"


def test_health_detalle_informa_el_comando_de_arranque(client, token_admin, monkeypatch):
    """HU-12 esc. 4 / HU-55 esc. 1: el operador comprueba en producción que el
    proceso arrancó con scripts.arrancar_api (1 worker, proxy de confianza) y
    no con un comando escrito a mano en el panel de la plataforma."""
    monkeypatch.delenv("COLDCARE_ARRANQUE", raising=False)
    monkeypatch.delenv("COLDCARE_WORKERS", raising=False)
    sin_lanzador = client.get("/health/detalle", headers=auth_header(token_admin)).json()
    assert sin_lanzador["arranque"] == {"lanzador": None, "workers": None}

    monkeypatch.setenv("COLDCARE_ARRANQUE", "scripts.arrancar_api")
    monkeypatch.setenv("COLDCARE_WORKERS", "1")
    cuerpo = client.get("/health/detalle", headers=auth_header(token_admin)).json()
    assert cuerpo["arranque"] == {"lanzador": "scripts.arrancar_api", "workers": 1}
    # El bloque del proxy sigue presente (B-11).
    assert {"esquema", "ip_cliente", "forwarded_allow_ips"} <= set(cuerpo["proxy"])
