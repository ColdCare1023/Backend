"""HU-24 / HU-25: `docs/operacion/HASH_CANONICO.md` describe exactamente la
fórmula que usa el sistema. Se ejecuta el código del documento (sección
«Recalcular un eslabón») y se compara con `hash_encadenado.py` en v1, v2 y v3,
con campos nulos, vacíos y ausentes (HU-24 esc. 4)."""

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.domain.value_objects.hash_encadenado import GENESIS_HASH, hash_esperado, timestamp_canonico

DOCUMENTO = Path(__file__).resolve().parents[2] / "docs" / "operacion" / "HASH_CANONICO.md"
CLAVE = b"k" * 32


@pytest.fixture(scope="module")
def documentado() -> dict:
    texto = DOCUMENTO.read_text(encoding="utf-8")
    seccion = texto.split("## Recalcular un eslabón", 1)[1]
    codigo = re.search(r"```python\n(.*?)```", seccion, flags=re.DOTALL).group(1)
    espacio: dict = {}
    exec(compile(codigo, str(DOCUMENTO), "exec"), espacio)
    return espacio


PAYLOADS = [
    {"device_id": "ESP32_FARMACIA_01", "temperatura_interna": 5.25, "unidad": "°C"},
    {"a": None},
    {"a": ""},
    {},
    {"anidado": {"z": 1, "a": [3, 2, 1]}, "usuario_id": uuid.UUID(int=7)},
]


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("payload", PAYLOADS)
def test_el_codigo_documentado_reproduce_el_hash_del_sistema(documentado, version, payload):
    instante = timestamp_canonico(datetime(2026, 11, 14, 10, 4, 5, 123456, tzinfo=timezone(timedelta(hours=-5))))
    clave = CLAVE if version == 3 else None

    esperado = hash_esperado(version, "ESP32_FARMACIA_01", GENESIS_HASH, instante, payload, clave)
    documentado_hash = documentado["recalcular"](version, "ESP32_FARMACIA_01", GENESIS_HASH, instante, payload, clave)

    assert documentado_hash == esperado
    assert re.fullmatch(r"[0-9a-f]{64}", documentado_hash)


def test_v3_sin_clave_no_concluye(documentado):
    assert documentado["recalcular"](3, "SISTEMA", GENESIS_HASH, "2026-11-14T15:04:05+00:00", {}, None) is None
    assert hash_esperado(3, "SISTEMA", GENESIS_HASH, "2026-11-14T15:04:05+00:00", {}, None) is None


def test_semilla_y_timestamp_documentados(documentado):
    assert documentado["GENESIS_HASH"] == GENESIS_HASH == "0" * 64
    for valor in (
        datetime(2026, 11, 14, 15, 4, 5),
        datetime(2026, 11, 14, 10, 4, 5, 123456, tzinfo=timezone(timedelta(hours=-5))),
    ):
        assert documentado["timestamp_canonico"](valor) == timestamp_canonico(valor)
    assert timestamp_canonico(datetime(2026, 11, 14, 15, 4, 5)) == "2026-11-14T15:04:05+00:00"


def test_nulo_vacio_y_ausente_dan_hashes_distintos(documentado):
    instante = "2026-11-14T15:04:05+00:00"
    hashes = {
        documentado["recalcular"](3, "SISTEMA", GENESIS_HASH, instante, payload, CLAVE)
        for payload in ({"a": None}, {"a": ""}, {})
    }
    assert len(hashes) == 3


def test_v1_no_incluye_chain_id(documentado):
    """La fórmula legada no lleva chain_id: el mismo evento en dos cadenas da el mismo hash."""
    instante = "2026-11-14T15:04:05+00:00"
    assert documentado["recalcular"](1, "A", GENESIS_HASH, instante, {"x": 1}) == documentado["recalcular"](
        1, "B", GENESIS_HASH, instante, {"x": 1}
    )
    assert documentado["recalcular"](2, "A", GENESIS_HASH, instante, {"x": 1}) != documentado["recalcular"](
        2, "B", GENESIS_HASH, instante, {"x": 1}
    )
