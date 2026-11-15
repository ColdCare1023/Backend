"""HU-55 esc. 1 y 3 / HU-12 esc. 4: un solo comando de arranque, sea cual sea el
builder de Railway (Dockerfile o Railpack), con 1 worker y confianza en el proxy.

Producción construye con Railpack, que no usa el CMD del Dockerfile: el
comando dependía del panel y nadie comprobaba `--workers 1` ni
`--forwarded-allow-ips`. Ahora el Procfile, railpack.json, railway.json y el
Dockerfile apuntan a `scripts.arrancar_api`, y estas pruebas lo fijan."""

import os

import pytest
import uvicorn
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from scripts import arrancar_api


def test_por_defecto_un_worker_proxy_de_confianza_y_puerto_8000():
    configuracion = arrancar_api.configuracion_uvicorn({})

    assert configuracion == {
        "app": "src.interface.main:app",
        "host": "0.0.0.0",
        "port": 8000,
        "workers": 1,
        "proxy_headers": True,
        # El mismo valor por defecto que tenía el CMD del Dockerfile.
        "forwarded_allow_ips": "127.0.0.1",
    }


def test_toma_el_puerto_y_la_red_del_proxy_del_entorno():
    configuracion = arrancar_api.configuracion_uvicorn(
        {"PORT": "8123", "FORWARDED_ALLOW_IPS": "100.64.0.0/10"}
    )

    assert configuracion["port"] == 8123
    assert configuracion["forwarded_allow_ips"] == "100.64.0.0/10"


def test_web_concurrency_no_cambia_el_unico_worker():
    """Uvicorn toma WEB_CONCURRENCY si no se le fija `workers`: con dos workers
    habría dos consumidores MQTT con el mismo client_id expulsándose."""
    configuracion = arrancar_api.configuracion_uvicorn({"WEB_CONCURRENCY": "4"})

    assert configuracion["workers"] == 1
    assert uvicorn.Config(**configuracion).workers == 1


def test_todas_las_vias_de_despliegue_arrancan_igual():
    """Railpack (Procfile / railpack.json), config-as-code (railway.json) y la
    imagen (Dockerfile) usan exactamente el mismo comando."""
    comandos = arrancar_api.comandos_de_arranque()

    assert comandos == {
        "Procfile": "python -m scripts.arrancar_api",
        "railpack.json": "python -m scripts.arrancar_api",
        "railway.json": "python -m scripts.arrancar_api",
        "Dockerfile": "python -m scripts.arrancar_api",
    }


def test_un_archivo_sin_comando_se_detecta(tmp_path):
    (tmp_path / "Procfile").write_text("worker: python otro.py\n", encoding="utf-8")

    comandos = arrancar_api.comandos_de_arranque(tmp_path)

    assert comandos == {"Procfile": None, "railpack.json": None, "railway.json": None, "Dockerfile": None}


def test_main_marca_el_lanzador_y_arranca_uvicorn(monkeypatch):
    llamadas = []
    monkeypatch.setattr(uvicorn, "run", lambda **kw: llamadas.append(kw))
    monkeypatch.delenv("COLDCARE_ARRANQUE", raising=False)
    monkeypatch.delenv("COLDCARE_WORKERS", raising=False)
    monkeypatch.setenv("PORT", "9000")

    arrancar_api.main()

    assert llamadas == [arrancar_api.configuracion_uvicorn({"PORT": "9000"})]
    assert os.environ["COLDCARE_ARRANQUE"] == "scripts.arrancar_api"
    assert os.environ["COLDCARE_WORKERS"] == "1"


async def _cliente_visto(trusted: str, cliente: str, cabeceras: list[tuple[bytes, bytes]]) -> tuple:
    visto = {}

    async def app(scope, receive, send):
        visto["client"] = scope["client"]
        visto["scheme"] = scope["scheme"]

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted)
    scope = {"type": "http", "scheme": "http", "client": (cliente, 40000), "headers": cabeceras}
    await middleware(scope, None, None)
    return visto["client"][0], visto["scheme"]


@pytest.mark.parametrize(
    ("red_del_proxy", "ip_esperada", "esquema_esperado"),
    [
        # Proxy de confianza: el backend ve la IP del navegador y https.
        ("100.64.0.0/10", "203.0.113.9", "https"),
        # Con el valor por defecto (solo 127.0.0.1) el backend ve la del proxy.
        ("127.0.0.1", "100.64.3.7", "http"),
    ],
)
async def test_la_ip_del_cliente_es_la_real_solo_si_el_proxy_es_de_confianza(
    red_del_proxy, ip_esperada, esquema_esperado
):
    """B-11: lo que hace `--proxy-headers` con FORWARDED_ALLOW_IPS. Un cliente
    que inventa X-Forwarded-For no lo consigue: el borde anexa la IP real a la
    derecha y uvicorn toma la primera IP que no es de confianza."""
    trusted = arrancar_api.configuracion_uvicorn({"FORWARDED_ALLOW_IPS": red_del_proxy})["forwarded_allow_ips"]
    cabeceras = [(b"x-forwarded-for", b"198.51.100.66, 203.0.113.9"), (b"x-forwarded-proto", b"https")]

    ip, esquema = await _cliente_visto(trusted, "100.64.3.7", cabeceras)

    assert (ip, esquema) == (ip_esperada, esquema_esperado)
