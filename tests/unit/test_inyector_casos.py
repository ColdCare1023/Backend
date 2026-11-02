"""El inyector de casos de escritorio arma los mensajes y el veredicto correctos."""

import importlib.util
import json
from pathlib import Path

import pytest

RUTA = Path(__file__).resolve().parents[2] / "infra-local/scripts/inyector_casos.py"
spec = importlib.util.spec_from_file_location("inyector_casos", RUTA)
inyector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inyector)


def _cuerpos(casos, nombre):
    return [json.loads(inyector.cuerpo_de(m)) for m in casos[nombre]]


def test_tem05_usa_los_valores_de_la_matriz_con_dos_normales_antes():
    casos = inyector.construir_casos(1000)
    temps = [c["temp_ds18b20"] for c in _cuerpos(casos, "tem05_limites")]
    assert temps[:2] == [4.5, 4.5]  # historial normal antes de los límites
    assert temps[2:8] == [1.9, 1.9375, 2.0, 8.0, 8.0625, 8.1]
    assert temps[8] != temps[8]  # NaN
    assert temps[9] is None  # sensor en falla


def test_tem05_declara_que_valores_son_excursion():
    mensajes = inyector.construir_casos(1000)["tem05_limites"]
    esperadas = {m["datos"]["temp_ds18b20"]: m["excursion"] for m in mensajes[2:8]}
    assert esperadas == {1.9: True, 1.9375: True, 2.0: False, 8.0: False, 8.0625: True, 8.1: True}
    assert mensajes[8]["esperado"] == "payload_rechazado"
    assert mensajes[9]["datos"]["ds18b20_status"] == "SENSOR_FAULT" and mensajes[9]["excursion"] is False


def test_el_veredicto_de_excursion_exige_coincidir_cuando_hay_dato():
    assert inyector.veredicto_excursion(True, True) and inyector.veredicto_excursion(False, False)
    assert not inyector.veredicto_excursion(True, False)
    assert inyector.veredicto_excursion(None, True) and inyector.veredicto_excursion(True, None)


def test_latencia_alterna_fuera_y_dentro_de_rango_con_identidades_distintas():
    casos = inyector.construir_casos(1000, repetir=100)
    cuerpos = _cuerpos(casos, "latencia")
    assert len(cuerpos) == 200
    assert [c["temp_ds18b20"] for c in cuerpos[:4]] == [12.0, 5.0, 12.0, 5.0]
    assert len({(c["boot_id"], c["seq_no"]) for c in cuerpos}) == 200  # ninguna se repite


def test_la_colision_reutiliza_la_identidad_con_otro_valor():
    a, b = _cuerpos(inyector.construir_casos(1000), "colision")
    assert (a["boot_id"], a["seq_no"]) == (b["boot_id"], b["seq_no"])
    assert a["temp_ds18b20"] != b["temp_ds18b20"]
    esperados = [m["esperado"] for m in inyector.construir_casos(1000)["colision"]]
    assert esperados == ["commit_confirmado", "conflicto_payload"]


def test_el_dispositivo_ajeno_no_espera_acuse():
    (m,) = inyector.construir_casos(1000)["device_ajeno"]
    assert json.loads(inyector.cuerpo_de(m))["device_id"] != inyector.DEV
    assert m["esperado"] is None and not m["espera_acuse"]


@pytest.mark.parametrize(
    "esperado,acuse,ok",
    [("commit_confirmado", "commit_confirmado", True), ("commit_confirmado", "SIN ACUSE", False),
     (None, "SIN ACUSE", True), (None, "commit_confirmado", False), ("duplicado", "commit_confirmado", False)],
)
def test_veredicto(esperado, acuse, ok):
    assert inyector.veredicto(esperado, acuse) is ok


def test_la_hora_de_captura_se_toma_al_enviar_no_al_construir(monkeypatch):
    casos = inyector.construir_casos(1000, repetir=3)
    mensaje = casos["latencia"][0]
    assert mensaje["datos"]["captured_at"] is None
    horas = iter(["2026-11-01T10:00:00Z", "2026-11-01T10:00:02Z"])
    monkeypatch.setattr(inyector, "ahora_iso", lambda: next(horas))
    a, b = (json.loads(inyector.cuerpo_de(m))["captured_at"] for m in casos["latencia"][:2])
    assert a != b  # cada subida lleva su propia hora: 5 subidas son 5 episodios


def test_el_duplicado_y_la_colision_comparten_hora():
    casos = inyector.construir_casos(1000)
    for nombre in ("duplicado", "colision"):
        a, b = _cuerpos(casos, nombre)
        assert a["captured_at"] == b["captured_at"] is not None
