"""Ingesta, validación y calidad de datos del pipeline preventivo."""

import stat
import zipfile

import numpy as np
import pandas as pd
import pytest

from src.infrastructure.ai.prediccion_preventiva import calidad, ingesta
from src.infrastructure.ai.prediccion_preventiva.configuracion import ConfiguracionPreventiva
from tests.unit._preventivo_sintetico import lecturas

CONFIG = ConfiguracionPreventiva()

CSV_HOHENHEIM = (
    "Date;Time;Temperature (C);Humidity (%)\n"
    "02.09.2024;10:00:00;6.10;80.0\n"
    "02.09.2024;10:00:05;6.20;80.1\n"
    "Date;Time;Temperature (C);Humidity (%)\n"
    "02.09.2024;10:00:10;6.30;80.2\n"
)


def _informe():
    return ingesta.InformeImportacion(fuente="prueba")


# ── Hohenheim ────────────────────────────────────────────────────


def test_csv_hohenheim_valido_con_cabecera_repetida_y_conversion_a_utc():
    informe = _informe()
    df = ingesta.parsear_csv_hohenheim(CSV_HOHENHEIM, "SENSOR01", informe)
    assert len(df) == 3
    assert informe.descartes["cabecera_repetida"] == 1
    # 10:00 CEST = 08:00 UTC.
    assert df["timestamp"].iloc[0] == pd.Timestamp("2024-09-02 08:00:00", tz="UTC")
    assert df["temperatura_interna"].tolist() == [6.1, 6.2, 6.3]


def test_csv_hohenheim_con_otro_separador_se_lee_igual():
    con_coma = CSV_HOHENHEIM.replace(";", ",")
    a = ingesta.parsear_csv_hohenheim(CSV_HOHENHEIM, "S", _informe())
    b = ingesta.parsear_csv_hohenheim(con_coma, "S", _informe())
    pd.testing.assert_frame_equal(a, b)


def test_csv_hohenheim_fechas_invalidas_se_descartan_y_se_cuentan():
    texto = CSV_HOHENHEIM + "31.02.2024;10:00:15;6.4;80.0\nxx;yy;6.5;80.0\n"
    informe = _informe()
    df = ingesta.parsear_csv_hohenheim(texto, "S", informe)
    assert len(df) == 3
    assert informe.descartes["timestamp_invalido"] == 2


def test_csv_hohenheim_sin_cabecera_es_error_de_esquema():
    with pytest.raises(ingesta.ErrorEsquemaDatos):
        ingesta.parsear_csv_hohenheim("02.09.2024;10:00:00;6.1;80.0\n", "S", _informe())


def test_csv_hohenheim_con_columnas_faltantes_es_error_de_esquema():
    texto = "Date;Time;Temperature (C);Humidity (%)\n02.09.2024;10:00:00\n02.09.2024;10:00:05\n"
    with pytest.raises(ingesta.ErrorEsquemaDatos):
        ingesta.parsear_csv_hohenheim(texto, "S", _informe())


# ── Hospital ─────────────────────────────────────────────────────

CABECERA = "_id,__v,createdAt,datetime,doorStatus,dsTemperature,humidity,time_slot,username\n"


def _csv_hospital(tmp_path, cuerpo: str, cabecera: str = CABECERA):
    ruta = tmp_path / "hospital.csv"
    ruta.write_text(cabecera + cuerpo, encoding="utf-8")
    return ruta


def test_hospital_filas_de_12_campos_no_desplazan_valores(tmp_path):
    cuerpo = (
        "a,0,2026-08-21T17:17:15.909Z,2026-08-21T17:17:15.000Z,closed,5.1,62,2026-08-21T17:10:00.000Z,MHT\n"
        "b,0,2026-08-22T12:12:01.369Z,2026-08-22T12:12:00.000Z,open,4.8,60,2026-08-22T12:12:00.000Z,MHT,"
        "6a88,21.6,2026-08-22T12:12:01.369Z\n"
    )
    lect, informe, tabla = ingesta.importar_hospital(_csv_hospital(tmp_path, cuerpo))
    assert lect["temperatura_interna"].tolist() == [5.1, 4.8]
    assert lect["puerta_abierta"].tolist() == [0.0, 1.0]
    assert tabla["temp_aux"].iloc[1] == 21.6
    assert tabla["campos"].tolist() == [9, 12]
    assert informe.advertencias, "Debe advertir de las columnas sin nombre"


def test_hospital_temperatura_vacia_es_nan_y_se_cuenta(tmp_path):
    cuerpo = "a,0,x,2026-08-21T17:28:03.000Z,closed,,60,x,BLD\n"
    lect, informe, _ = ingesta.importar_hospital(_csv_hospital(tmp_path, cuerpo))
    assert np.isnan(lect["temperatura_interna"].iloc[0])
    assert informe.descartes["dsTemperature_vacia"] == 1


def test_hospital_sin_columnas_obligatorias_es_error_de_esquema(tmp_path):
    ruta = _csv_hospital(tmp_path, "a,0,x\n", cabecera="_id,__v,createdAt\n")
    with pytest.raises(ingesta.ErrorEsquemaDatos):
        ingesta.importar_hospital(ruta)


def test_coldcare_sin_timestamp_es_error_de_esquema(tmp_path):
    ruta = tmp_path / "cc.csv"
    ruta.write_text("device_id,temperatura_interna\nESP32-01,5.0\n", encoding="utf-8")
    with pytest.raises(ingesta.ErrorEsquemaDatos):
        ingesta.importar_coldcare_csv(ruta)


def test_coldcare_valido(tmp_path):
    ruta = tmp_path / "cc.csv"
    ruta.write_text(
        "device_id,timestamp,temperatura_interna,apertura_refrigerador\n"
        "ESP32-01,2026-10-01T10:00:00-05:00,5.0,false\nESP32-01,2026-10-01T10:00:30-05:00,5.1,true\n",
        encoding="utf-8",
    )
    lect, _ = ingesta.importar_coldcare_csv(ruta)
    assert lect["timestamp"].iloc[0] == pd.Timestamp("2026-10-01 15:00:00", tz="UTC")
    assert lect["puerta_abierta"].tolist() == [0.0, 1.0]


# ── Calidad ──────────────────────────────────────────────────────


def test_limpiar_elimina_duplicados_exactos_y_anula_conflictos():
    df = lecturas([5.0, 5.1, 5.2])
    duplicados = pd.concat([df, df.iloc[[1]], df.iloc[[2]].assign(temperatura_interna=9.9)], ignore_index=True)
    limpio, informe = calidad.limpiar(duplicados)
    assert informe.descartes["duplicados_exactos"] == 1
    assert informe.descartes["timestamps_en_conflicto"] == 2
    assert limpio["temperatura_interna"].tolist() == [5.0, 5.1]


def test_limpiar_ordena_saltos_temporales():
    df = lecturas([5.0, 5.1, 5.2]).iloc[[2, 0, 1]]
    limpio, _ = calidad.limpiar(df)
    assert limpio["timestamp"].is_monotonic_increasing


def test_temperatura_fisicamente_imposible_se_anula():
    limpio, informe = calidad.limpiar(lecturas([5.0, 150.0, 5.2]))
    assert np.isnan(limpio["temperatura_interna"].iloc[1])
    assert informe.descartes["temperaturas_fisicamente_imposibles"] == 1


def test_ultracongelador_se_clasifica_incompatible():
    assert calidad.clasificar_equipo(pd.Series([-31.5, -30.9, -32.0])) == calidad.EQUIPO_ULTRACONGELACION
    assert calidad.clasificar_equipo(pd.Series([4.5, 5.0])) == calidad.EQUIPO_REFRIGERACION


def test_remuestreo_no_depende_de_la_frecuencia_de_muestreo():
    rampa_5s = lecturas(np.linspace(4.0, 6.0, 12 * 60), paso_s=5)
    rampa_60s = lecturas(np.linspace(4.0, 6.0, 60), paso_s=60)
    r5 = calidad.preparar_rejillas(rampa_5s, CONFIG).set_index("g_ns")["temperatura"]
    r60 = calidad.preparar_rejillas(rampa_60s, CONFIG).set_index("g_ns")["temperatura"]
    comunes = r5.index.intersection(r60.index)[2:]
    assert len(comunes) > 50
    assert np.allclose(r5[comunes], r60[comunes], atol=0.05)


def test_remuestreo_tolera_fluctuacion_del_reloj():
    df = lecturas(np.full(240, 5.0), paso_s=60, jitter_s=1.5, semilla=3)
    rejilla = calidad.preparar_rejillas(df, CONFIG)
    assert rejilla["temperatura"].isna().sum() == 0
    assert rejilla["segmento_id"].nunique() == 1


def test_sensor_desconectado_corta_la_serie_en_segmentos():
    df = lecturas(np.full(120, 5.0))
    df = df[(df.index < 40) | (df.index >= 50)]  # 10 min sin datos
    rejilla = calidad.preparar_rejillas(df, CONFIG)
    assert rejilla["segmento_id"].nunique() == 2


def test_hueco_corto_no_corta_la_serie():
    df = lecturas(np.full(120, 5.0))
    df = df[df.index != 40]  # una sola lectura perdida
    assert calidad.preparar_rejillas(df, CONFIG)["segmento_id"].nunique() == 1


# ── ZIP no confiable ─────────────────────────────────────────────


def _zip(tmp_path, miembros: dict[str, bytes], nombre="datos.zip", compresion=zipfile.ZIP_DEFLATED):
    ruta = tmp_path / nombre
    with zipfile.ZipFile(ruta, "w", compression=compresion) as zf:
        for n, contenido in miembros.items():
            zf.writestr(n, contenido)
    return ruta


def test_zip_valido_se_lee_en_memoria(tmp_path):
    ruta = _zip(tmp_path, {"SENSOR01.CSV": CSV_HOHENHEIM.encode(), "leeme.txt": b"x"})
    contenido = ingesta.leer_zip_seguro(ruta, extensiones=(".csv",))
    assert list(contenido) == ["SENSOR01.CSV"]


def test_zip_con_path_traversal_se_rechaza(tmp_path):
    ruta = _zip(tmp_path, {"../../fuera.csv": b"a;b\n"})
    with pytest.raises(ingesta.ErrorArchivoInseguro):
        ingesta.leer_zip_seguro(ruta)


def test_zip_con_ruta_absoluta_se_rechaza(tmp_path):
    ruta = _zip(tmp_path, {"/etc/x.csv": b"a;b\n"})
    with pytest.raises(ingesta.ErrorArchivoInseguro):
        ingesta.leer_zip_seguro(ruta)


def test_zip_bomba_por_razon_de_compresion_se_rechaza(tmp_path):
    ruta = _zip(tmp_path, {"a.csv": b"0" * 5_000_000})
    with pytest.raises(ingesta.ErrorArchivoInseguro):
        ingesta.leer_zip_seguro(ruta)


def test_zip_corrupto_se_rechaza(tmp_path):
    ruta = tmp_path / "roto.zip"
    ruta.write_bytes(b"PK\x03\x04 esto no es un zip")
    with pytest.raises(ingesta.ErrorArchivoInseguro):
        ingesta.leer_zip_seguro(ruta)


def test_zip_con_crc_alterado_se_rechaza(tmp_path):
    ruta = _zip(tmp_path, {"a.csv": b"Date;Time\n" * 50}, compresion=zipfile.ZIP_STORED)
    datos = bytearray(ruta.read_bytes())
    posicion = datos.find(b"Date;Time")
    datos[posicion] = ord("X")
    ruta.write_bytes(bytes(datos))
    with pytest.raises(ingesta.ErrorArchivoInseguro):
        ingesta.leer_zip_seguro(ruta)


def test_zip_con_enlace_simbolico_se_rechaza(tmp_path):
    ruta = tmp_path / "enlace.zip"
    with zipfile.ZipFile(ruta, "w") as zf:
        info = zipfile.ZipInfo("enlace.csv")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(ingesta.ErrorArchivoInseguro):
        ingesta.leer_zip_seguro(ruta)


def test_zip_con_demasiados_miembros_se_rechaza(tmp_path):
    ruta = _zip(tmp_path, {f"{i}.csv": b"a" for i in range(5)})
    with pytest.raises(ingesta.ErrorArchivoInseguro):
        ingesta.leer_zip_seguro(ruta, limite_miembros=3)


def test_csv_de_texto_no_utf8_es_error_de_esquema(tmp_path):
    ruta = _zip(tmp_path, {"SENSOR01.CSV": "Date;Tíme\n".encode("latin-1") + b"\xff\xfe"})
    acciones = tmp_path / "acciones.csv"
    acciones.write_text("Date;Action;Start Time;End Time\n2024-09-02;Begin;09:45:00;09:45:00\n", encoding="utf-8")
    with pytest.raises(ingesta.ErrorEsquemaDatos):
        ingesta.importar_hohenheim(ruta, acciones)


def test_puerta_del_protocolo_se_asigna_por_intervalo(tmp_path):
    acciones = tmp_path / "acciones.csv"
    acciones.write_text(
        "Date;Action;Start Time;End Time\n2024-09-02;Door opened;10:00:00;10:00:10\n", encoding="utf-8"
    )
    ruta = _zip(tmp_path, {"SENSOR01.CSV": CSV_HOHENHEIM.encode()})
    lect, _, _ = ingesta.importar_hohenheim(ruta, acciones)
    assert lect["puerta_abierta"].tolist() == [1.0, 1.0, 0.0]


def test_lectura_de_zip_no_escribe_en_disco(tmp_path):
    ruta = _zip(tmp_path, {"../escape.csv": b"x"})
    antes = {p.name for p in tmp_path.parent.iterdir()}
    with pytest.raises(ingesta.ErrorArchivoInseguro):
        ingesta.leer_zip_seguro(ruta)
    assert {p.name for p in tmp_path.parent.iterdir()} == antes
    assert not (tmp_path.parent / "escape.csv").exists()
