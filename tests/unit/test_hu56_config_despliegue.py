"""HU-56: configuración que exige el despliegue en Railway."""

import pytest

from src.infrastructure.config import Settings, normalizar_database_url

_SECRETO = "s" * 64


@pytest.mark.parametrize(
    ("entrada", "esperada"),
    [
        ("postgres://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        ("postgresql://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        ("postgresql+asyncpg://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        (
            "postgresql://u:p@host:5432/db?sslmode=require",
            "postgresql+asyncpg://u:p@host:5432/db?ssl=require",
        ),
        ("sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
    ],
)
def test_database_url_se_normaliza_a_asyncpg(entrada, esperada):
    assert normalizar_database_url(entrada) == esperada
    assert Settings(database_url=entrada).database_url == esperada


def _produccion(**extra) -> dict:
    base = {
        "environment": "production",
        "jwt_secret_key": _SECRETO,
        "trazabilidad_hmac_key": "h" * 40,
        "database_url": "postgresql://u:p@db.railway.internal:5432/railway",
        "allowed_hosts": ["api.up.railway.app", "healthcheck.railway.app"],
        "cors_origins": ["https://coldcare.vercel.app"],
        "mqtt_enabled": False,
    }
    base.update(extra)
    return base


def test_produccion_minima_de_railway_es_valida():
    settings = Settings(**_produccion())
    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_correo_por_api_http_exige_clave_y_frontend_url():
    with pytest.raises(ValueError, match="EMAIL_API_KEY"):
        Settings(**_produccion(
            email_enabled=True, email_transporte="http_api",
            email_from="avisos@coldcare.pe", email_destino_respaldo="qf@coldcare.pe",
            frontend_url="https://coldcare.vercel.app",
        ))
    with pytest.raises(ValueError, match="FRONTEND_URL"):
        Settings(**_produccion(
            email_enabled=True, email_transporte="http_api", email_api_key="re_x",
            email_from="avisos@coldcare.pe", email_destino_respaldo="qf@coldcare.pe",
        ))


def test_smtp_enabled_sigue_siendo_sinonimo_del_correo():
    settings = Settings(smtp_enabled=True)
    assert settings.correo_habilitado
    with pytest.raises(ValueError, match="remitente"):
        Settings(**_produccion(smtp_enabled=True, frontend_url="https://coldcare.vercel.app"))
