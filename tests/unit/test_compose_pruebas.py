"""El compose del banco de pruebas pasa al contenedor variables que Settings reconoce."""

import re
from pathlib import Path

from src.infrastructure.config import Settings

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = (RAIZ / "infra-local/docker-compose.pruebas.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (RAIZ / ".env.example").read_text(encoding="utf-8")


def _variables_del_backend() -> list[str]:
    bloque = COMPOSE.split("\n  backend:\n", 1)[1].split("\n    extra_hosts:", 1)[0]
    return re.findall(r"^      ([A-Z][A-Z0-9_]+):", bloque, flags=re.MULTILINE)


def test_cada_variable_del_backend_es_un_campo_de_settings():
    campos = {nombre.upper() for nombre in Settings.model_fields}
    desconocidas = [v for v in _variables_del_backend() if v not in campos]
    assert desconocidas == []


def test_estan_las_variables_que_destraban_los_casos():
    esperadas = {
        "IA_PREVENTIVO_MODO", "IA_PRUEBA_RETARDO_S", "IA_PRUEBA_EXCEPCION", "MQTT_PRUEBA_FALLO_ANTES_COMMIT",
        "EMAIL_ENABLED", "SMTP_HOST", "SMTP_PORT", "SMTP_SEGURIDAD",
        "ALERTA_VENTANA_NORMALIZACION_MINUTOS", "SIN_DATOS_PERIODO_SEGUNDOS", "SIN_DATOS_N_INTERVALOS",
    }
    assert esperadas <= set(_variables_del_backend())


def test_usa_una_base_aparte_y_nunca_produccion():
    assert "coldcare_prueba" in COMPOSE
    assert "ENVIRONMENT: development" in COMPOSE
    assert not re.search(r"ENVIRONMENT:\s*production", COMPOSE)


def test_la_imagen_de_la_base_va_fijada_por_digest():
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", i) for i in re.findall(r"^\s*image:\s*(\S+)", COMPOSE, re.M))


def test_el_env_example_documenta_las_opciones_de_prueba():
    for nombre in ("IA_INFERENCIA_TIMEOUT_S", "IA_PRUEBA_RETARDO_S", "IA_PRUEBA_EXCEPCION",
                   "MQTT_PRUEBA_FALLO_ANTES_COMMIT", "MQTT_PRUEBA_FALLO_MARCA"):
        assert f"{nombre}=" in ENV_EXAMPLE
