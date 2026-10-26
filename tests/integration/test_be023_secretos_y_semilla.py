"""BE-023: secretos publicados en el historial y credenciales semilla.

- Producción rechaza cualquier secreto cuyo SHA-256 figure entre los valores
  que quedaron publicados en el historial del repositorio.
- `seed_dev` no contiene contraseñas, se niega a correr en producción y usa
  contraseñas del entorno o aleatorias.
- Los scripts de infra-local y el simulador no tienen contraseñas por defecto.
"""

import argparse
import hashlib
import importlib.util
import re
import string
from pathlib import Path

import pytest

from scripts import seed_dev, simulador_esp32
from src.infrastructure import config
from src.infrastructure.config import Settings
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.security.password_hasher import verify_password

RAIZ = Path(__file__).resolve().parents[2]
VALOR_DE_PRUEBA = "valor-de-prueba-publicado-en-un-historial-git"


def _produccion(**sobrescribir) -> Settings:
    valores = {
        "environment": "production",
        "jwt_secret_key": "j" * 48,
        "trazabilidad_hmac_key": "h" * 48,
        "allowed_hosts": ["api.ejemplo.pe"],
        "cors_origins": ["https://app.ejemplo.pe"],
        "database_url": "postgresql+asyncpg://u:p@db/real",
        "mqtt_enabled": False,
    }
    valores.update(sobrescribir)
    return Settings(**valores)


# ── Secretos publicados ───────────────────────────────────────────────────


@pytest.fixture
def valor_publicado(monkeypatch):
    huella = hashlib.sha256(VALOR_DE_PRUEBA.encode()).hexdigest()
    monkeypatch.setattr(config, "_HASHES_SECRETOS_PUBLICADOS", config._HASHES_SECRETOS_PUBLICADOS | {huella})
    return VALOR_DE_PRUEBA


@pytest.mark.parametrize("campo", ["jwt_secret_key", "trazabilidad_hmac_key"])
def test_produccion_rechaza_un_secreto_publicado(valor_publicado, campo):
    with pytest.raises(ValueError, match="historial"):
        _produccion(**{campo: valor_publicado})


def test_produccion_acepta_secretos_no_publicados():
    assert _produccion().environment == "production"


def test_la_lista_incluye_el_secreto_del_historial_y_no_su_valor():
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in config._HASHES_SECRETOS_PUBLICADOS)
    assert len(config._HASHES_SECRETOS_PUBLICADOS) >= 1


# ── seed_dev ──────────────────────────────────────────────────────────────


def test_seed_no_contiene_contrasenas_sino_variables_de_entorno():
    for _nombre, _email, variable, _rol in seed_dev.USUARIOS_DEV:
        assert re.fullmatch(r"SEED_[A-Z]+_PASSWORD", variable)


def test_seed_se_niega_a_correr_en_produccion(monkeypatch):
    monkeypatch.setattr(seed_dev, "get_settings", lambda: _produccion())

    with pytest.raises(SystemExit):
        seed_dev.exigir_entorno_no_productivo()


def test_password_generada_cumple_la_politica_y_es_aleatoria():
    generadas = {seed_dev.generar_password() for _ in range(20)}

    assert len(generadas) == 20
    for password in generadas:
        assert len(password) >= 10
        assert any(c in string.ascii_letters for c in password)
        assert any(c in string.digits for c in password)


@pytest.mark.asyncio
async def test_seed_usa_la_contrasena_del_entorno_o_genera_una(db_session_factory, monkeypatch, capsys):
    monkeypatch.setattr(seed_dev, "_session_factory", db_session_factory)
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "Admin-local-2026")
    monkeypatch.delenv("SEED_FARMACEUTICO_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_TECNICO_PASSWORD", raising=False)

    await seed_dev.main()

    salida = capsys.readouterr().out
    generada = re.search(r"farmaceutico@farmacia\.demo\.pe .*: (\S+)$", salida, flags=re.MULTILINE).group(1)
    assert "Admin-local-2026" not in salida
    async with db_session_factory() as session:
        repo = SQLAlchemyUsuarioRepository(session)
        admin = await repo.obtener_por_email("admin@farmacia.demo.pe")
        farmaceutico = await repo.obtener_por_email("farmaceutico@farmacia.demo.pe")
    assert verify_password("Admin-local-2026", admin.password_hash)
    assert verify_password(generada, farmaceutico.password_hash)


# ── infra-local y simulador ──────────────────────────────────────────────


def _aprovisionar():
    ruta = RAIZ / "infra-local/scripts/aprovisionar.py"
    especificacion = importlib.util.spec_from_file_location("aprovisionar_be023", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def test_aprovisionar_lee_la_contrasena_semilla_del_entorno(monkeypatch):
    aprovisionar = _aprovisionar()
    monkeypatch.setattr(aprovisionar, "_env_local", dict)
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "Admin-local-2026")

    assert aprovisionar.credenciales_semilla("Admin Demo") == ("admin@farmacia.demo.pe", "Admin-local-2026")


def test_aprovisionar_sin_contrasena_semilla_se_detiene(monkeypatch):
    aprovisionar = _aprovisionar()
    monkeypatch.setattr(aprovisionar, "_env_local", dict)
    monkeypatch.delenv("SEED_FARMACEUTICO_PASSWORD", raising=False)

    with pytest.raises(SystemExit, match="SEED_FARMACEUTICO_PASSWORD"):
        aprovisionar.credenciales_semilla("Farmaceutico Demo")


@pytest.mark.asyncio
async def test_simulador_sin_contrasena_no_intenta_iniciar_sesion():
    args = argparse.Namespace(password=None, base_url="http://127.0.0.1:9", device_id="X", intervalo=1.0, n=1,
                              forzar_alerta=False, forzar_cada=0)

    assert await simulador_esp32.ejecutar(args) == 2
