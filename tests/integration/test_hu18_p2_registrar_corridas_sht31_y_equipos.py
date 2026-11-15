"""HU-18 P-2: la ablación con el SHT31 y las corridas de otro equipo.

1. `corridas` evalúa siempre `termico_minimo` y `termico_ambiente` (ablación
   del SHT31). Antes, si una corrida de prueba o de validación no tenía
   temperatura y humedad ambiental válidas (o el CSV no traía esas columnas,
   como el formato mínimo del paso 1 de IA_PREVENTIVA_ACTIVACION.md), la
   partición de `termico_ambiente` lanzaba ValueError y la CLI se detenía con
   código 2, también al registrar `termico_minimo`, que no usa el SHT31, y
   con un mensaje falso ("Corridas de prueba inexistentes: ['C4']"). Ahora
   solo el conjunto que se registra (o `termico_minimo` en el informe) es
   obligatorio; el otro queda "no estimable" con el motivo real.
2. Si ninguna corrida de ajuste o validación es del equipo de las de prueba no
   hay `separacion_min`: el registro sigue (sirve en sombra) pero avisa que
   POST /activar responderá 409 `faltan_particiones`."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd
import pytest

from src.application.use_cases.gestionar_modelo_ia import (
    BLOQUEO_FALTAN_PARTICIONES,
    motivos_bloqueo_activacion,
)
from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.value_objects.motivo_bloqueo import codigo_de
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva.__main__ import main
from tests.corridas_sinteticas import escribir_corridas

VERSION = "rf-corridas-h30-sht31-0.1.0"
COLUMNAS_SHT31 = ["temperatura_ambiental", "humedad_ambiental"]


def _sin_sht31(csv, corridas=None, quitar_columnas=False):
    """Celdas del SHT31 vacías (en `corridas`, o en todas) o sin sus columnas."""
    df = pd.read_csv(csv)
    if quitar_columnas:
        df = df.drop(columns=COLUMNAS_SHT31)
    else:
        filas = df["corrida_id"].isin(corridas) if corridas else pd.Series(True, index=df.index)
        df.loc[filas, COLUMNAS_SHT31] = float("nan")
    df.to_csv(csv, index=False)
    return csv


def _registrar(csv, raiz, salida, *extra):
    return main([
        "corridas", "--csv", str(csv), "--prueba", "C4,C5", *extra, "--registrar", "--version", VERSION,
        "--directorio-registro", str(raiz), "--salida", str(salida),
    ])


@pytest.mark.parametrize("corrida", ["C4", "C3"], ids=["prueba_sin_sht31", "validacion_sin_sht31"])
def test_una_corrida_sin_sht31_no_impide_registrar_termico_minimo(tmp_path, capsys, corrida):
    csv = _sin_sht31(escribir_corridas(tmp_path / "corridas.csv", corridas=6), [corrida])
    raiz, salida = tmp_path / "registro", tmp_path / "informe.json"

    codigo = _registrar(csv, raiz, salida, "--validacion", "C3")

    assert codigo == 0, capsys.readouterr().err
    informe = json.loads(salida.read_text(encoding="utf-8"))
    ambiente = informe["resultados"]["termico_ambiente"]["resultado"]
    assert ambiente.startswith("no estimable")
    assert f"las corridas ['{corrida}'] no tienen observaciones válidas para el conjunto termico_ambiente" in ambiente
    assert "inexistentes" not in ambiente and "SHT31" in ambiente
    assert informe["resultados"]["termico_minimo"]["resultado"] == "evaluado"
    assert informe["ablacion_sht31"] is None
    manifiesto = registro.leer_manifiesto(VERSION, raiz)
    assert manifiesto["conjunto_features"] == "termico_minimo"
    particiones = manifiesto["particiones"]
    assert (particiones["validacion"], particiones["prueba"]) == (["C3"], ["C4", "C5"])


@pytest.mark.parametrize("quitar_columnas", [True, False], ids=["csv_sin_columnas_sht31", "sht31_siempre_vacio"])
def test_sin_sht31_en_el_banco_el_informe_y_el_registro_siguen(tmp_path, capsys, quitar_columnas):
    csv = _sin_sht31(escribir_corridas(tmp_path / "corridas.csv", corridas=5), quitar_columnas=quitar_columnas)
    salida = tmp_path / "informe.json"

    assert main(["corridas", "--csv", str(csv), "--prueba", "C4,C5", "--salida", str(salida)]) == 0, (
        capsys.readouterr().err
    )
    informe = json.loads(salida.read_text(encoding="utf-8"))
    assert informe["resultados"]["termico_minimo"]["resultado"] == "evaluado"
    assert informe["resultados"]["termico_ambiente"]["resultado"].startswith("no estimable: las corridas ['C4', 'C5']")
    assert informe["ablacion_sht31"] is None

    assert _registrar(csv, tmp_path / "registro", tmp_path / "registro.json") == 0, capsys.readouterr().err


def test_registrar_termico_ambiente_sin_sht31_en_la_prueba_se_niega_con_el_motivo_real(tmp_path, capsys):
    """El conjunto que se registra sí es obligatorio: sin el SHT31 en una
    corrida de prueba no hay modelo que registrar, y el mensaje lo dice."""
    csv = _sin_sht31(escribir_corridas(tmp_path / "corridas.csv", corridas=5), ["C4"])
    raiz = tmp_path / "registro"

    codigo = main([
        "corridas", "--csv", str(csv), "--prueba", "C4,C5", "--registrar", "--version", VERSION,
        "--conjunto", "termico_ambiente", "--directorio-registro", str(raiz),
    ])

    assert codigo == 2
    error = capsys.readouterr().err
    assert error.startswith("No se registra el modelo: las corridas no se pueden usar: ")
    assert "las corridas ['C4'] no tienen observaciones válidas para el conjunto termico_ambiente" in error
    assert not raiz.exists()


def _a_version_bd(cuerpo: dict) -> VersionModeloIA:
    return VersionModeloIA(
        version=cuerpo["version"], model_hash=cuerpo["model_hash"],
        feature_schema_version=cuerpo["feature_schema_version"], dataset_version=cuerpo["dataset_version"],
        scikit_learn_version=cuerpo["scikit_learn_version"], python_version=cuerpo["python_version"],
        trained_at=datetime.fromisoformat(cuerpo["trained_at"]).astimezone(timezone.utc),
        aprobado_por=uuid4(), horizonte_min=cuerpo["horizonte_min"],
        metricas=cuerpo["metricas"], particiones=cuerpo["particiones"],
    )


def test_prueba_en_otro_equipo_registra_y_avisa_que_activar_respondera_faltan_particiones(tmp_path, capsys):
    """Antes la CLI registraba sin decir nada y el cuerpo que imprimía, sin
    `separacion_min`, recibía 409 `faltan_particiones` en /activar."""
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=5)
    df = pd.read_csv(csv)
    df.loc[df["corrida_id"].isin(["C4", "C5"]), "device_id"] = "ESP32_BANCO_02"
    df.to_csv(csv, index=False)

    codigo = main([
        "corridas", "--csv", str(csv), "--prueba", "C4,C5", "--registrar", "--version", VERSION,
        "--directorio-registro", str(tmp_path / "registro"),
    ])

    salida = capsys.readouterr()
    assert codigo == 0, salida.err
    resumen = json.loads(salida.out)
    (advertencia,) = resumen["advertencias"]
    assert "409 faltan_particiones" in advertencia and "no escriba una separación a mano" in advertencia
    assert f"Advertencia: {advertencia}" in salida.err
    cuerpo = resumen["cuerpo_registro_api"]
    assert resumen["particiones"]["separacion_min"] is None and "separacion_min" not in cuerpo["particiones"]
    # La advertencia describe la regla real de /activar.
    codigos = [codigo_de(m) for m in motivos_bloqueo_activacion(_a_version_bd(cuerpo), "por_incidente")]
    assert BLOQUEO_FALTAN_PARTICIONES in codigos


def test_con_la_prueba_en_el_mismo_equipo_no_hay_advertencias(tmp_path, capsys):
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=5)

    assert main([
        "corridas", "--csv", str(csv), "--prueba", "C4,C5", "--registrar", "--version", VERSION,
        "--directorio-registro", str(tmp_path / "registro"),
    ]) == 0

    salida = capsys.readouterr()
    resumen = json.loads(salida.out)
    assert resumen["advertencias"] == [] and "Advertencia" not in salida.err
    cuerpo = resumen["cuerpo_registro_api"]
    codigos = [codigo_de(m) for m in motivos_bloqueo_activacion(_a_version_bd(cuerpo), "por_incidente")]
    assert BLOQUEO_FALTAN_PARTICIONES not in codigos
