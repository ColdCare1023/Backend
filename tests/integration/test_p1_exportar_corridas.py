"""P-1: exportador de corridas a CSV (`scripts/exportar_corridas.py`).

Base temporal (SQLite en archivo y PostgreSQL real) con lecturas de dos corridas: columnas del plan,
`timestamp` = COALESCE(timestamp_estimado, timestamp), `corrida_id` desde el manifiesto, exclusión de
lecturas manuales y simuladas, viaje de ida y vuelta por `ingesta.importar_coldcare_csv` y conexión
de solo lectura."""

import asyncio
import csv
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from scripts import exportar_corridas as exp
from src.infrastructure.ai.prediccion_preventiva import ingesta
from src.infrastructure.database.base import Base
from src.infrastructure.database.models import DeviceModel, ThermalReadingModel
from tests.postgres import a_asyncpg, base_migrada

DEV, OTRO = "ESP32_BANCO_PRUEBA", "ESP32_OTRO_PRUEBA"
DOC_REPRODUCIBILIDAD = Path(__file__).resolve().parents[2] / "docs/ml/09_REPRODUCIBILITY.md"
T0 = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)

MANIFIESTO = (
    "corrida_id,device_id,inicio,fin,tipo,uso,mqtt_host,responsable\n"
    f"C01,{DEV},2026-11-14T10:00:00Z,2026-11-14T10:05:00Z,S-C,entrenamiento,192.168.18.21,DS\n"
    f"C02,{DEV},2026-11-14T10:05:00Z,2026-11-14T10:10:00+00:00,S-P3,prueba,192.168.18.21,DS\n"
    f"C03,{OTRO},2026-11-14T10:00:00Z,2026-11-14T11:00:00Z,S-B,excluida,192.168.18.21,DS\n"
)


def _s(segundos: float) -> datetime:
    return T0 + timedelta(seconds=segundos)


def _lecturas() -> list[dict]:
    v1 = {"contrato": "1.0", "sensor_fault": False, "simulated": False}
    # Mismas claves en todas las filas: un INSERT de varias filas toma las columnas de la primera.
    base = {"device_id": DEV, "origen": "dispositivo", "time_quality": "synced", "apertura_refrigerador": False,
            "temperatura_ambiental": 21.0, "humedad_ambiental": 55.0, "boot_id": 1, "timestamp_estimado": None}
    return [
        {**base, "seq_no": 1, "timestamp": _s(0), "temperatura_interna": 5.0, "payload": v1},
        # DS18B20 en falla.
        {**base, "seq_no": 2, "timestamp": _s(10), "temperatura_interna": None,
         "payload": {**v1, "sensor_fault": True, "ds18b20_fault": "no_response"}},
        # Solo el SHT31 en falla: la temperatura interna es válida.
        {**base, "seq_no": 3, "timestamp": _s(20), "temperatura_interna": 5.2, "temperatura_ambiental": None,
         "humedad_ambiental": None, "apertura_refrigerador": True, "payload": {**v1, "sensor_fault": True}},
        # Sin reloj, reconstruida: llegó a los 35 s, se capturó a los 30 s.
        {**base, "seq_no": 4, "timestamp": _s(35), "timestamp_estimado": _s(30), "time_quality": "unsynced",
         "temperatura_interna": 5.3, "payload": v1},
        # Sin reloj ni reconstrucción: su hora es la de llegada.
        {**base, "seq_no": 5, "timestamp": _s(45), "time_quality": "unsynced", "temperatura_interna": 5.4,
         "payload": v1},
        # Simulada (demo) y manual: nunca entran.
        {**base, "seq_no": 6, "timestamp": _s(50), "temperatura_interna": 12.5, "payload": {**v1, "simulated": True}},
        {**base, "boot_id": None, "seq_no": None, "timestamp": _s(55), "temperatura_interna": 9.9,
         "origen": "manual", "payload": None},
        # Borde: 10:05:00 es el fin (excluido) de C01 y el inicio de C02.
        {**base, "seq_no": 7, "timestamp": _s(300), "temperatura_interna": 6.0, "payload": v1},
        # Firmware anterior (sin contrato v1): sensor_fault desconocido.
        {**base, "boot_id": None, "seq_no": None, "timestamp": _s(360), "time_quality": None,
         "temperatura_interna": 6.1, "apertura_refrigerador": None, "payload": {"firmware_version": "1.0.0"}},
        # Llegó a las 10:20 pero se capturó a las 10:07: cuenta la hora de captura (C02).
        {**base, "seq_no": 8, "timestamp": _s(1200), "timestamp_estimado": _s(420), "time_quality": "unsynced",
         "temperatura_interna": 6.2, "payload": v1},
        # Fuera de toda ventana.
        {**base, "seq_no": 9, "timestamp": _s(700), "temperatura_interna": 6.3, "payload": v1},
        # Otro dispositivo, en una corrida excluida.
        {**base, "device_id": OTRO, "seq_no": 1, "timestamp": _s(60), "temperatura_interna": 1.5, "payload": v1},
    ]


async def _poblar(url: str, crear: bool) -> None:
    motor = create_async_engine(url)
    try:
        async with motor.begin() as conexion:
            if crear:
                await conexion.run_sync(Base.metadata.create_all)
            await conexion.execute(insert(DeviceModel), [{"id": DEV}, {"id": OTRO}])
            await conexion.execute(insert(ThermalReadingModel), _lecturas())
    finally:
        await motor.dispose()


async def _contar(url: str) -> int:
    motor = create_async_engine(url)
    try:
        async with motor.connect() as conexion:
            return (await conexion.execute(select(func.count()).select_from(ThermalReadingModel))).scalar_one()
    finally:
        await motor.dispose()


@pytest.fixture
def base_sqlite(tmp_path):
    ruta = tmp_path / "coldcare prueba.db"  # con espacio, como la ruta real del repositorio
    url = f"sqlite+aiosqlite:///{ruta}"
    asyncio.run(_poblar(url, crear=True))
    return ruta, url


@pytest.fixture
def manifiesto(tmp_path):
    ruta = tmp_path / "hojas.csv"
    ruta.write_text(MANIFIESTO, encoding="utf-8")
    return ruta


def _filas(ruta):
    with ruta.open(encoding="utf-8", newline="") as archivo:
        return list(csv.DictReader(archivo))


def _exportar(url, manifiesto, salida):
    return asyncio.run(exp.exportar(url, exp.leer_manifiesto(manifiesto), salida))


def test_exporta_las_columnas_del_plan_con_hora_de_captura_y_corrida(base_sqlite, manifiesto, tmp_path):
    _, url = base_sqlite
    salida = tmp_path / "corridas.csv"
    resumen = _exportar(url, manifiesto, salida)

    with salida.open(encoding="utf-8") as archivo:
        cabecera = archivo.readline().strip().split(",")
    assert cabecera[:8] == [
        "device_id", "timestamp", "temperatura_interna", "apertura_refrigerador", "time_quality",
        "ds18b20_status", "sensor_fault", "corrida_id",
    ]
    filas = _filas(salida)
    assert [(f["corrida_id"], f["seq_no"]) for f in filas] == [
        ("C01", "1"), ("C01", "2"), ("C01", "3"), ("C01", "4"), ("C01", "5"),
        ("C02", "7"), ("C02", ""), ("C02", "8"),
    ]
    por_seq = {f["seq_no"]: f for f in filas}
    assert (por_seq["1"]["timestamp"], por_seq["1"]["temperatura_interna"], por_seq["1"]["ds18b20_status"]) == (
        "2026-11-14T10:00:00.000+00:00", "5.0", "OK",
    )
    assert (por_seq["2"]["temperatura_interna"], por_seq["2"]["ds18b20_status"], por_seq["2"]["sensor_fault"]) == (
        "", "SENSOR_FAULT", "true",
    )
    # Falla solo del SHT31: el DS18B20 sigue en OK aunque sensor_fault (contrato) sea true.
    assert (por_seq["3"]["ds18b20_status"], por_seq["3"]["sensor_fault"], por_seq["3"]["apertura_refrigerador"]) == (
        "OK", "true", "true",
    )
    assert por_seq["3"]["temperatura_ambiental"] == ""
    # COALESCE(timestamp_estimado, timestamp) y de dónde sale la hora.
    assert (por_seq["4"]["timestamp"], por_seq["4"]["time_quality"], por_seq["4"]["origen_hora"]) == (
        "2026-11-14T10:00:30.000+00:00", "unsynced", "reconstruida",
    )
    assert (por_seq["5"]["timestamp"], por_seq["5"]["origen_hora"]) == ("2026-11-14T10:00:45.000+00:00", "recepcion")
    assert (por_seq["8"]["timestamp"], por_seq["8"]["corrida_id"]) == ("2026-11-14T10:07:00.000+00:00", "C02")
    legado = por_seq[""]
    assert (legado["sensor_fault"], legado["time_quality"], legado["apertura_refrigerador"]) == ("", "", "")

    assert resumen["prueba"] == "C02"
    assert resumen["omitidas_por_uso"] == ["C03"]
    c01 = next(c for c in resumen["corridas"] if c["corrida_id"] == "C01")
    assert (c01["filas"], c01["ds18b20_sensor_fault"], c01["unsynced"], c01["simuladas_omitidas"]) == (5, 1, 2, 1)
    assert c01["mqtt_host"] == "192.168.18.21" and c01["tipo"] == "S-C"
    assert resumen["filas"] == 8 and resumen["corridas_sin_lecturas"] == []


def test_ida_y_vuelta_por_el_importador_del_pipeline(base_sqlite, manifiesto, tmp_path):
    _, url = base_sqlite
    salida = tmp_path / "corridas.csv"
    _exportar(url, manifiesto, salida)

    lecturas, informe = ingesta.importar_coldcare_csv(salida)
    assert informe.filas_leidas == 8
    assert informe.descartes["time_quality_unsynced"] == 3  # seq 4, 5 y 8
    assert informe.descartes["sensor_fault"] == 1  # solo la falla del DS18B20 (seq 2), no la del SHT31
    assert sorted(lecturas["serie_id"].unique()) == [f"{DEV}#C01", f"{DEV}#C02"]
    assert set(lecturas["corrida_id"]) == {"C01", "C02"}
    assert lecturas["temperatura_interna"].notna().sum() == 4  # 5.0, 5.2, 6.0, 6.1


def test_la_exportacion_deja_el_archivo_sqlite_intacto(base_sqlite, manifiesto, tmp_path):
    """El exportador no escribe por su cuenta. No prueba el solo lectura: con un motor normal un
    SELECT tampoco cambiaría el archivo. Eso lo prueban los DELETE de las pruebas siguientes."""
    ruta, url = base_sqlite
    antes = hashlib.sha256(ruta.read_bytes()).hexdigest()
    _exportar(url, manifiesto, tmp_path / "corridas.csv")
    assert hashlib.sha256(ruta.read_bytes()).hexdigest() == antes


def test_el_motor_del_exportador_no_puede_escribir_en_sqlite(base_sqlite):
    _, url = base_sqlite

    async def _intentar():
        motor = exp.motor_solo_lectura(url)
        try:
            async with motor.begin() as conexion:
                await conexion.execute(text("DELETE FROM thermal_readings"))
        finally:
            await motor.dispose()

    with pytest.raises(OperationalError, match="readonly"):
        asyncio.run(_intentar())
    assert asyncio.run(_contar(url)) == len(_lecturas())


@pytest.mark.parametrize(
    "url",
    ["mysql+aiomysql://u:p@h/db", "sqlite+aiosqlite:///:memory:", "postgresql+psycopg://u:p@h/db",
     "sqlite+aiosqlite:///no/existe.db"],
)
def test_destinos_que_no_se_pueden_abrir_en_solo_lectura_se_rechazan(url):
    with pytest.raises(exp.ErrorEntrada):
        exp.motor_solo_lectura(url)


def test_un_error_a_mitad_no_deja_un_csv_parcial(base_sqlite, manifiesto, tmp_path, monkeypatch):
    _, url = base_sqlite
    salida = tmp_path / "corridas.csv"

    def _fallar(*_args):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(exp, "fila_csv", _fallar)
    with pytest.raises(RuntimeError):
        _exportar(url, manifiesto, salida)
    assert not salida.exists()
    assert list(tmp_path.glob("*.parcial")) == []


# ── Manifiesto ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("contenido", "error"),
    [
        (f"corrida_id,device_id,inicio\nC1,{DEV},2026-11-14T10:00:00Z\n", "faltan columnas"),
        (f"corrida_id,device_id,inicio,fin,color\nC1,{DEV},2026-11-14T10:00:00Z,2026-11-14T11:00:00Z,a\n",
         "columnas desconocidas"),
        (f"corrida_id,device_id,inicio,fin\nC1,{DEV},2026-11-14T10:00:00,2026-11-14T11:00:00Z\n", "no tiene zona"),
        (f"corrida_id,device_id,inicio,fin\nC1,{DEV},2026-11-14T11:00:00Z,2026-11-14T10:00:00Z\n", "posterior"),
        (f"corrida_id,device_id,inicio,fin\nC#1,{DEV},2026-11-14T10:00:00Z,2026-11-14T11:00:00Z\n", "inválido"),
        (f"corrida_id,device_id,inicio,fin,uso\nC1,{DEV},2026-11-14T10:00:00Z,2026-11-14T11:00:00Z,test\n", "uso"),
        ("corrida_id,device_id,inicio,fin\nC1,a/b,2026-11-14T10:00:00Z,2026-11-14T11:00:00Z\n", "device_id"),
        (f"corrida_id,device_id,inicio,fin\nC1,{DEV},2026-11-14T10:00:00Z,2026-11-14T11:00:00Z\n"
         f"C1,{DEV},2026-11-14T12:00:00Z,2026-11-14T13:00:00Z\n", "repetido"),
        (f"corrida_id,device_id,inicio,fin\nC1,{DEV},2026-11-14T10:00:00Z,2026-11-14T11:00:00Z\n"
         f"C2,{DEV},2026-11-14T10:59:00Z,2026-11-14T12:00:00Z\n", "se solapan"),
        ("corrida_id,device_id,inicio,fin\n", "no tiene corridas"),
    ],
)
def test_manifiesto_invalido(tmp_path, contenido, error):
    ruta = tmp_path / "hojas.csv"
    ruta.write_text(contenido, encoding="utf-8")
    with pytest.raises(exp.ErrorEntrada, match=error):
        exp.leer_manifiesto(ruta)


def test_corridas_contiguas_de_un_dispositivo_y_solapadas_de_otro_se_admiten(tmp_path):
    ruta = tmp_path / "hojas.csv"
    ruta.write_text(
        "corrida_id,device_id,inicio,fin\n"
        f"C2,{DEV},2026-11-14T11:00:00Z,2026-11-14T12:00:00Z\n"
        f"C1,{DEV},2026-11-14T10:00:00Z,2026-11-14T11:00:00Z\n"
        f"C3,{OTRO},2026-11-14T10:30:00Z,2026-11-14T11:30:00Z\n",
        encoding="utf-8",
    )
    assert [c.corrida_id for c in exp.leer_manifiesto(ruta)] == ["C1", "C2", "C3"]


# ── CLI ──────────────────────────────────────────────────────────────────


def test_main_escribe_csv_y_resumen_y_no_sobrescribe(base_sqlite, manifiesto, tmp_path, capsys):
    _, url = base_sqlite
    salida, resumen = tmp_path / "corridas.csv", tmp_path / "resumen.json"
    argv = ["--manifiesto", str(manifiesto), "--salida", str(salida), "--resumen", str(resumen),
            "--database-url", url]
    assert exp.main(argv) == 0
    assert json.loads(resumen.read_text(encoding="utf-8"))["prueba"] == "C02"
    assert len(_filas(salida)) == 8
    capsys.readouterr()
    assert exp.main(argv) == 2
    assert "ya existe" in capsys.readouterr().err
    assert exp.main([*argv, "--sobrescribir"]) == 0


def test_main_sin_base_no_exporta(manifiesto, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    salida = tmp_path / "corridas.csv"
    assert exp.main(["--manifiesto", str(manifiesto), "--salida", str(salida)]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
    assert not salida.exists()


# ── PostgreSQL real ──────────────────────────────────────────────────────


def test_postgres_exporta_y_la_conexion_es_de_solo_lectura(manifiesto, tmp_path):
    with base_migrada() as dsn:
        url = a_asyncpg(dsn)
        asyncio.run(_poblar(url, crear=False))
        salida = tmp_path / "corridas.csv"
        resumen = _exportar(url, manifiesto, salida)
        assert resumen["filas"] == 8
        filas = _filas(salida)
        assert [f["timestamp"] for f in filas if f["seq_no"] == "8"] == ["2026-11-14T10:07:00.000+00:00"]
        assert [f["temperatura_interna"] for f in filas if f["seq_no"] == "1"] == ["5.0"]

        async def _intentar_escribir():
            motor = exp.motor_solo_lectura(url)
            try:
                async with motor.begin() as conexion:
                    await conexion.execute(text("DELETE FROM thermal_readings"))
            finally:
                await motor.dispose()

        with pytest.raises(DBAPIError, match="read-only"):
            asyncio.run(_intentar_escribir())
        assert asyncio.run(_contar(url)) == len(_lecturas())


def _consulta_psql_del_documento(corrida) -> str:
    """El SELECT del `COPY` de 09_REPRODUCIBILITY.md, con la corrida de ejemplo cambiada por `corrida`."""
    texto = DOC_REPRODUCIBILIDAD.read_text(encoding="utf-8")
    bloque = re.search(r"COPY \((SELECT .*?)\) TO STDOUT", texto, re.DOTALL)
    assert bloque, "no se encontró la consulta COPY en 09_REPRODUCIBILITY.md"
    consulta = bloque.group(1)
    for ejemplo, valor in (
        ("'D01'", f"'{corrida.corrida_id}'"),
        ("'ESP32_FARMACIA_01'", f"'{corrida.device_id}'"),
        ("'2026-12-01T00:00:00Z'", f"'{corrida.inicio.isoformat()}'"),
        ("'2026-12-29T00:00:00Z'", f"'{corrida.fin.isoformat()}'"),
    ):
        assert ejemplo in consulta, ejemplo
        consulta = consulta.replace(ejemplo, valor)
    return consulta


def _texto_pg(valor) -> str:
    if isinstance(valor, datetime):
        return exp._utc(valor).isoformat(timespec="milliseconds")
    return exp._texto(valor)


def test_la_consulta_psql_del_documento_da_las_columnas_y_filas_del_exportador(manifiesto, tmp_path):
    """09_REPRODUCIBILITY.md la presenta como alternativa al exportador: mismas 13 columnas (con
    `origen_hora`) y mismas filas, salvo el formato que decide psql."""
    corridas = [c for c in exp.leer_manifiesto(manifiesto) if c.uso != "excluida"]
    with base_migrada() as dsn:
        url = a_asyncpg(dsn)
        asyncio.run(_poblar(url, crear=False))
        salida = tmp_path / "corridas.csv"
        _exportar(url, manifiesto, salida)
        esperadas = _filas(salida)

        async def _consultar():
            motor = create_async_engine(url)
            try:
                async with motor.connect() as conexion:
                    filas = []
                    for corrida in corridas:
                        resultado = await conexion.exec_driver_sql(_consulta_psql_del_documento(corrida))
                        columnas = list(resultado.keys())
                        filas += [dict(zip(columnas, map(_texto_pg, fila), strict=True)) for fila in resultado]
                    return columnas, filas
            finally:
                await motor.dispose()

        columnas, filas = asyncio.run(_consultar())
    assert columnas == list(exp.COLUMNAS)
    assert {f["origen_hora"] for f in filas} == {"nodo", "reconstruida", "recepcion"}
    assert filas == esperadas
