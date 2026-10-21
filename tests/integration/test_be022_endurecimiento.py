"""BE-022: endurecimiento de despliegue y configuración.

- Las respuestas de los límites de la API (413/429) llevan las cabeceras de
  seguridad y las de CORS.
- `JWT_ALGORITHM` solo admite algoritmos HMAC.
- Imagen, compose y CI: no root, imágenes y acciones fijadas, migraciones como
  paso aparte, sin secretos en el contexto de build.
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.infrastructure.config import Settings, get_settings

RAIZ = Path(__file__).resolve().parents[2]
ORIGEN = "http://localhost:5173"


def _cabeceras_de_seguridad(respuesta) -> None:
    assert respuesta.headers["X-Content-Type-Options"] == "nosniff"
    assert respuesta.headers["X-Frame-Options"] == "DENY"
    assert respuesta.headers["Cache-Control"] == "no-store"


# ── Middlewares ───────────────────────────────────────────────────────────


def test_el_413_lleva_cabeceras_de_seguridad_y_cors(client):
    respuesta = client.post(
        "/api/lecturas",
        content=b"{}",
        headers={"Content-Length": str(get_settings().max_body_bytes + 1), "Origin": ORIGEN,
                 "Content-Type": "application/json"},
    )

    assert respuesta.status_code == 413
    _cabeceras_de_seguridad(respuesta)
    assert respuesta.headers["Access-Control-Allow-Origin"] == ORIGEN


def test_el_429_global_lleva_cabeceras_de_seguridad_y_expone_retry_after(client, app):
    limitador = app.state.api_rate_limiter
    for _ in range(get_settings().api_rate_limit_max_solicitudes):
        limitador.registrar_fallo("testclient")

    respuesta = client.get("/api/alertas", headers={"Origin": ORIGEN})

    assert respuesta.status_code == 429
    _cabeceras_de_seguridad(respuesta)
    assert respuesta.headers["Access-Control-Allow-Origin"] == ORIGEN
    assert "retry-after" in respuesta.headers["Access-Control-Expose-Headers"].lower()
    assert int(respuesta.headers["Retry-After"]) > 0


def test_una_respuesta_normal_sigue_llevando_las_cabeceras(client):
    respuesta = client.get("/health", headers={"Origin": ORIGEN})

    assert respuesta.status_code == 200
    assert respuesta.headers["X-Content-Type-Options"] == "nosniff"
    assert respuesta.headers["Access-Control-Allow-Origin"] == ORIGEN


# ── Configuración ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("algoritmo", ["none", "RS256", "ES256", "hs256"])
def test_jwt_algorithm_rechaza_lo_que_no_es_hmac(algoritmo):
    with pytest.raises(ValidationError):
        Settings(jwt_algorithm=algoritmo)


@pytest.mark.parametrize("algoritmo", ["HS256", "HS384", "HS512"])
def test_jwt_algorithm_admite_hmac(algoritmo):
    assert Settings(jwt_algorithm=algoritmo).jwt_algorithm == algoritmo


# ── Artefactos de despliegue ─────────────────────────────────────────────


def test_dockerfile_no_root_imagen_fijada_y_sin_migraciones_en_el_arranque():
    dockerfile = (RAIZ / "infra-local/backend/Dockerfile").read_text(encoding="utf-8")
    instrucciones = [linea for linea in dockerfile.splitlines() if linea and not linea.startswith("#")]

    assert re.match(r"^FROM \S+@sha256:[0-9a-f]{64}$", instrucciones[0])
    usuario = [linea for linea in instrucciones if linea.startswith("USER ")]
    assert usuario and usuario[-1].split()[1] not in ("root", "0")
    cmd = next(linea for linea in instrucciones if linea.startswith("CMD "))
    assert "alembic" not in cmd
    assert "--require-hashes" in dockerfile


def test_compose_fija_imagenes_y_migra_antes_del_backend():
    compose = (RAIZ / "infra-local/docker-compose.yml").read_text(encoding="utf-8")

    imagenes = re.findall(r"^\s*image:\s*(\S+)", compose, flags=re.MULTILINE)
    assert imagenes
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", imagen) for imagen in imagenes)
    assert "migraciones: { condition: service_completed_successfully }" in compose
    assert "DEVICE_REGISTRY_ESTRICTO: ${DEVICE_REGISTRY_ESTRICTO:-true}" in compose


def test_ci_fija_las_acciones_por_sha():
    ci = (RAIZ / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    acciones = re.findall(r"uses:\s*(\S+)", ci)
    assert acciones
    assert all(re.search(r"@[0-9a-f]{40}$", accion) for accion in acciones)


def test_el_contexto_de_build_excluye_secretos():
    ignorados = (RAIZ / ".dockerignore").read_text(encoding="utf-8").splitlines()

    for patron in (".env", ".git", "infra-local/.env", "infra-local/certs", "infra-local/secrets"):
        assert patron in ignorados
