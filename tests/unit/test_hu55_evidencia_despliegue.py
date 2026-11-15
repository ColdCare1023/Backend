"""HU-55 esc. 4: `scripts.evidencia_despliegue` deja la evidencia del despliegue
y es seguro de ejecutar sin red ni credenciales (lo corre un operador después)."""

import json

import httpx
import pytest

from scripts import evidencia_despliegue

BASE = "https://backend.ejemplo.test"
TOKEN = "eyJ.token-de-prueba.firma"


def _detalle(**cambios) -> dict:
    cuerpo = {
        "status": "ok",
        "db": "ok",
        "alembic_revision": "0042_bigint_identidad",
        "alembic_head": "0042_bigint_identidad",
        "mqtt": {"habilitado": True, "tarea_activa": True, "conectado": True},
        "arranque": {"lanzador": "scripts.arrancar_api", "workers": 1},
        "proxy": {"esquema": "https", "ip_cliente": "8.8.4.4", "forwarded_allow_ips": "100.64.0.0/10"},
    }
    cuerpo.update(cambios)
    return cuerpo


def _backend(detalle: dict) -> httpx.MockTransport:
    def responder(peticion: httpx.Request) -> httpx.Response:
        ruta = peticion.url.path
        if ruta == "/health":
            return httpx.Response(200, json={"status": "ok"}, headers={"strict-transport-security": "max-age=63072000"})
        if ruta == "/health/detalle":
            if peticion.headers.get("authorization") != f"Bearer {TOKEN}":
                return httpx.Response(401, json={"detail": "No autenticado"})
            return httpx.Response(200, json=detalle)
        return httpx.Response(404, json={"detail": "Not Found"})

    return httpx.MockTransport(responder)


def test_despliegue_correcto_pasa_todas_las_verificaciones():
    evidencia = evidencia_despliegue.recolectar(BASE, TOKEN, transport=_backend(_detalle()))

    assert all(evidencia["verificaciones"].values()), evidencia["verificaciones"]
    assert evidencia["configuracion_repositorio"]["comando_esperado"] == "python -m scripts.arrancar_api"


@pytest.mark.parametrize(
    ("cambios", "verificacion"),
    [
        ({"arranque": {"lanzador": None, "workers": None}}, "arranque_reproducible"),
        ({"arranque": {"lanzador": "scripts.arrancar_api", "workers": 2}}, "arranque_reproducible"),
        ({"proxy": {"esquema": "http", "ip_cliente": "8.8.4.4"}}, "proxy_ve_https"),
        ({"proxy": {"esquema": "https", "ip_cliente": "100.64.3.7"}}, "ip_cliente_publica"),
        ({"proxy": {"esquema": "https", "ip_cliente": "10.0.0.4"}}, "ip_cliente_publica"),
        ({"mqtt": {"conectado": False}}, "mqtt_conectado"),
        # HU-55 esc. 1: el pre-deploy no corrió (base en 0041, código en 0042).
        ({"alembic_revision": "0041_hu20_normalizacion_preventiva"}, "migraciones_aplicadas"),
        # Un backend anterior que no informa su cabeza no se da por migrado.
        ({"alembic_head": None}, "migraciones_aplicadas"),
    ],
)
def test_cada_desvio_se_detecta(cambios, verificacion):
    evidencia = evidencia_despliegue.recolectar(BASE, TOKEN, transport=_backend(_detalle(**cambios)))

    assert evidencia["verificaciones"][verificacion] is False


def test_sin_red_no_lanza_y_sale_con_1(tmp_path, monkeypatch, capsys):
    def sin_red(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no se pudo resolver el host", request=peticion)

    monkeypatch.setenv("BACKEND_URL", BASE)
    monkeypatch.setenv("EVIDENCIA_TOKEN", TOKEN)
    salida = tmp_path / "evidencia.json"

    codigo = evidencia_despliegue.main(["--salida", str(salida)], transport=httpx.MockTransport(sin_red))

    assert codigo == 1
    evidencia = json.loads(salida.read_text(encoding="utf-8"))
    assert "ConnectError" in evidencia["error_conexion"]
    assert evidencia["verificaciones"]["liveness_ok"] is False
    # La comprobación del repositorio no necesita red.
    assert evidencia["verificaciones"]["repositorio_arranque_unico"] is True
    assert "Sin conexión" in capsys.readouterr().err


def test_el_token_nunca_se_escribe_en_la_evidencia(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKEND_URL", BASE)
    monkeypatch.setenv("EVIDENCIA_TOKEN", TOKEN)
    salida = tmp_path / "evidencia.json"

    assert evidencia_despliegue.main(["--salida", str(salida)], transport=_backend(_detalle())) == 0
    assert TOKEN not in salida.read_text(encoding="utf-8")


def test_sin_token_no_consulta_el_detalle_y_no_aprueba(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKEND_URL", BASE)
    monkeypatch.delenv("EVIDENCIA_TOKEN", raising=False)
    salida = tmp_path / "evidencia.json"

    assert evidencia_despliegue.main(["--salida", str(salida)], transport=_backend(_detalle())) == 1
    evidencia = json.loads(salida.read_text(encoding="utf-8"))
    assert evidencia["health_detalle"]["error"] == "sin EVIDENCIA_TOKEN"
    assert evidencia["verificaciones"]["db_ok"] is False


def test_un_cuerpo_que_no_es_json_no_rompe_la_evidencia():
    def html(peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Application not found</html>")

    evidencia = evidencia_despliegue.recolectar(BASE, TOKEN, transport=httpx.MockTransport(html))

    assert evidencia["health"]["cuerpo"] is None
    assert evidencia["verificaciones"]["db_ok"] is False
    assert evidencia["verificaciones"]["docs_ocultos"] is False


def test_sin_backend_url_sale_con_2(monkeypatch):
    monkeypatch.delenv("BACKEND_URL", raising=False)

    assert evidencia_despliegue.main([]) == 2
