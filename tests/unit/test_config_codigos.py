"""Validación de arranque de los códigos de verificación y del relé de Apps Script."""

import pytest

from src.infrastructure.config import Settings

_CLAVE = "c" * 48


def _produccion(**extra) -> dict:
    base = {
        "environment": "production",
        "_env_file": None,
        "jwt_secret_key": "s" * 64,
        "trazabilidad_hmac_key": "h" * 40,
        "database_url": "postgresql://u:p@db.railway.internal:5432/railway",
        "allowed_hosts": ["api.up.railway.app"],
        "cors_origins": ["https://coldcare.vercel.app"],
        "mqtt_enabled": False,
    }
    base.update(extra)
    return base


def _correo_apps_script(**extra) -> dict:
    base = {
        "email_enabled": True,
        "email_transporte": "apps_script",
        "email_api_url": "https://script.google.com/macros/s/AKfy/exec",
        "email_api_key": "secreto-compartido",
        "email_from": "avisos@coldcare.pe",
        "email_destino_respaldo": "qf@coldcare.pe",
        "frontend_url": "https://coldcare.vercel.app",
        "codigos_hmac_key": _CLAVE,
    }
    base.update(extra)
    return base


def test_produccion_acepta_apps_script_completo_con_otp():
    ajustes = Settings(**_produccion(**_correo_apps_script(otp_login_enabled=True)))
    assert ajustes.email_transporte == "apps_script" and ajustes.otp_login_enabled
    assert ajustes.clave_codigos == _CLAVE.encode()


@pytest.mark.parametrize(
    "cambio",
    [
        {"email_api_url": "http://script.google.com/macros/s/AKfy/exec"},
        {"email_api_url": "https://api.resend.com/emails"},  # el valor por defecto no es un relé
        {"email_api_key": ""},
    ],
)
def test_apps_script_exige_url_https_propia_y_secreto(cambio):
    with pytest.raises(ValueError, match="EMAIL_TRANSPORTE=apps_script"):
        Settings(**_produccion(**_correo_apps_script(**cambio)))


def test_otp_en_produccion_exige_correo():
    with pytest.raises(ValueError, match="OTP_LOGIN_ENABLED"):
        Settings(**_produccion(otp_login_enabled=True, codigos_hmac_key=_CLAVE))


@pytest.mark.parametrize(
    ("clave", "mensaje"),
    [
        ("", "al menos 32 bytes"),
        ("corta", "al menos 32 bytes"),
        ("s" * 64, "no puede reutilizar"),  # = JWT_SECRET_KEY
        ("h" * 40, "no puede reutilizar"),  # = TRAZABILIDAD_HMAC_KEY
    ],
)
def test_la_clave_de_codigos_en_produccion(clave, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        Settings(**_produccion(**_correo_apps_script(codigos_hmac_key=clave)))


def test_produccion_sin_correo_no_exige_clave_de_codigos_pero_no_la_inventa():
    ajustes = Settings(**_produccion())
    with pytest.raises(RuntimeError, match="CODIGOS_HMAC_KEY"):
        _ = ajustes.clave_codigos


def test_fuera_de_produccion_hay_una_clave_derivada_y_determinista():
    a = Settings(environment="test", _env_file=None, jwt_secret_key="x" * 48)
    b = Settings(environment="test", _env_file=None, jwt_secret_key="x" * 48)
    c = Settings(environment="test", _env_file=None, jwt_secret_key="y" * 48)
    assert a.clave_codigos == b.clave_codigos != c.clave_codigos
    assert a.clave_codigos != ("x" * 48).encode()  # no es la clave JWT tal cual


def test_valores_por_defecto_de_los_codigos():
    ajustes = Settings(environment="test", _env_file=None)
    assert (
        ajustes.otp_login_enabled,
        ajustes.codigo_ttl_minutos,
        ajustes.codigo_activacion_ttl_horas,
        ajustes.codigo_max_intentos,
        ajustes.codigo_reenvio_segundos,
        ajustes.codigo_max_fallos_cuenta,
        ajustes.codigo_ventana_fallos_horas,
        ajustes.email_from_nombre,
    ) == (False, 10, 48, 5, 60, 20, 24, "ColdCare")


def test_los_enlaces_de_los_correos_salen_de_frontend_url():
    ajustes = Settings(environment="test", _env_file=None, frontend_url="https://coldcare.vercel.app/")
    assert ajustes.url_codigo_cuenta == "https://coldcare.vercel.app/recuperar-contrasena"
    assert ajustes.url_restablecer == "https://coldcare.vercel.app/restablecer-password"
    propio = Settings(environment="test", _env_file=None, password_reset_url="https://app.x.pe/reset")
    assert propio.url_restablecer == "https://app.x.pe/reset" and propio.url_codigo_cuenta == "https://app.x.pe/recuperar-contrasena"


def _correo_http_api() -> dict:
    return {
        "email_enabled": True,
        "email_transporte": "http_api",
        "email_api_key": "re_clave",
        "email_from": "avisos@coldcare.pe",
        "email_destino_respaldo": "qf@coldcare.pe",
        "frontend_url": "https://coldcare.vercel.app",
        "codigos_hmac_key": _CLAVE,
    }


@pytest.mark.parametrize("correo", [_correo_apps_script, _correo_http_api])
def test_produccion_exige_enlace_de_recuperacion_https_con_cualquier_transporte(correo):
    """El enlace `#token=` sale por cualquier transporte, no solo por SMTP: con
    EMAIL_ENABLED y SMTP_ENABLED=false tampoco puede apuntar a una página sin TLS."""
    with pytest.raises(ValueError, match="PASSWORD_RESET_URL"):
        Settings(**_produccion(**correo(), password_reset_url="http://coldcare.vercel.app/restablecer-password"))
    # Sin PASSWORD_RESET_URL propia vale el respaldo en FRONTEND_URL (https).
    ajustes = Settings(**_produccion(**correo()))
    assert ajustes.url_restablecer == "https://coldcare.vercel.app/restablecer-password"
