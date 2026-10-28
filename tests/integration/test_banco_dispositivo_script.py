"""ADR-18: el script de banco da de alta un dispositivo demo, guarda su token sin
imprimirlo y se niega a rotar o dar de baja un equipo real."""

import importlib
import stat
from pathlib import Path

import pytest

from tests.conftest import auth_header

SCRIPTS = Path(__file__).resolve().parents[2] / "infra-local" / "scripts"


@pytest.fixture
def banco(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    modulo = importlib.import_module("banco_dispositivo")
    # El `client` de pruebas es un httpx.Client que atiende http://testserver.
    monkeypatch.setattr(modulo, "API", "http://testserver")
    monkeypatch.setattr(modulo, "RAIZ", tmp_path)
    return modulo


def _dispositivo(client, cabecera, device_id: str) -> dict:
    return next(d for d in client.get("/api/dispositivos", headers=cabecera).json() if d["id"] == device_id)


def test_alta_crea_un_demo_y_guarda_el_token_sin_imprimirlo(client, token_admin, banco, tmp_path, capsys):
    cabecera = auth_header(token_admin)

    banco.alta(client, cabecera, "ESP32_BANCO_01", aceptar_privacidad=False)

    archivo = tmp_path / "secrets" / "ESP32_BANCO_01.token"
    token = archivo.read_text()
    assert token and token not in capsys.readouterr().out
    assert stat.S_IMODE(archivo.stat().st_mode) == 0o600
    assert _dispositivo(client, cabecera, "ESP32_BANCO_01")["es_demo"] is True


def test_token_y_baja_no_tocan_un_equipo_real(client, token_admin, banco):
    cabecera = auth_header(token_admin)
    assert client.post("/api/dispositivos", json={"device_id": "ESP32_FARMACIA_01"}, headers=cabecera).status_code == 201

    with pytest.raises(SystemExit, match="no es un dispositivo demo"):
        banco.token(client, cabecera, "ESP32_FARMACIA_01")
    with pytest.raises(SystemExit, match="no es un dispositivo demo"):
        banco.baja(client, cabecera, "ESP32_FARMACIA_01", "error de tipeo")
    assert _dispositivo(client, cabecera, "ESP32_FARMACIA_01")["activo"] is True


def test_token_rota_y_baja_cierra_el_dispositivo_de_banco(client, token_admin, banco, tmp_path):
    cabecera = auth_header(token_admin)
    banco.alta(client, cabecera, "ESP32_BANCO_01", aceptar_privacidad=False)
    anterior = (tmp_path / "secrets" / "ESP32_BANCO_01.token").read_text()

    banco.token(client, cabecera, "ESP32_BANCO_01")
    assert (tmp_path / "secrets" / "ESP32_BANCO_01.token").read_text() != anterior

    banco.baja(client, cabecera, "ESP32_BANCO_01", "fin del ensayo ADR-18")
    assert _dispositivo(client, cabecera, "ESP32_BANCO_01")["activo"] is False
