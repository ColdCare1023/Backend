"""HU-31 / HU-38: el periodo se mide con la HORA DE CAPTURA en la serie y en
el resumen del reporte PDF, y la consulta acota `timestamp` por ambos lados.

- HU-38: `estadisticas_periodo` usaba `timestamp` (la LLEGADA de una lectura
  `unsynced`), mientras la curva usa la hora reconstruida: el mismo periodo
  daba cifras distintas en la curva y en el PDF.
- HU-31: `_en_periodo` no acotaba `timestamp` por arriba, así que la serie de
  un periodo antiguo recorría todas las filas posteriores hasta hoy. La cota es
  la antigüedad máxima de HU-07 (LECTURA_ANTIGUEDAD_MAX_HORAS)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from src.application.use_cases.exportar_reporte_bpa_pdf import _estadisticas_de_agregados
from src.infrastructure.config import get_settings
from src.infrastructure.database.models import DeviceModel, ThermalReadingModel
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.pdf.generador_pdf import GeneradorReporteBPAPDF

DEV = "ESP32-P2-CAPTURA"
DESDE = datetime(2026, 9, 1, tzinfo=timezone.utc)
HASTA = DESDE + timedelta(hours=24)


async def _lecturas(factory, *filas: dict) -> None:
    async with factory() as session:
        if await session.get(DeviceModel, DEV) is None:
            session.add(DeviceModel(id=DEV, created_at=DESDE - timedelta(days=30)))
            await session.flush()
        for fila in filas:
            session.add(ThermalReadingModel(**{
                "device_id": DEV, "temperatura_interna": 5.0, "estado_conectividad": "online",
                "apertura_refrigerador": False, "origen": "dispositivo", "riesgo_efectivo": "normal", **fila,
            }))
        await session.commit()


async def _sembrar_mezcla(factory) -> None:
    await _lecturas(
        factory,
        # Sincronizada dentro del periodo.
        {"timestamp": DESDE + timedelta(hours=1), "time_quality": "synced", "temperatura_interna": 4.0},
        # Llegó DESPUÉS del periodo, capturada dentro (hora reconstruida).
        {"timestamp": HASTA + timedelta(hours=2), "timestamp_estimado": DESDE + timedelta(hours=23),
         "time_quality": "unsynced", "temperatura_interna": 9.5, "riesgo_efectivo": "excursion_critica"},
        # Llegó DENTRO del periodo, capturada antes: no es de este periodo.
        {"timestamp": DESDE + timedelta(minutes=30), "timestamp_estimado": DESDE - timedelta(hours=3),
         "time_quality": "unsynced", "temperatura_interna": 1.0},
        # Sin hora de captura (unsynced sin reanclar), llegó dentro.
        {"timestamp": DESDE + timedelta(hours=5), "time_quality": "unsynced", "temperatura_interna": 6.0},
        # Manual: su hora es la que declaró la persona; cuenta en el reporte.
        {"timestamp": DESDE + timedelta(hours=6), "origen": "manual", "temperatura_interna": 5.0},
    )


@pytest.mark.asyncio
async def test_el_resumen_del_pdf_mide_el_periodo_por_hora_de_captura(db_session_factory):
    await _sembrar_mezcla(db_session_factory)
    async with db_session_factory() as session:
        agregados = await SQLAlchemyLecturaRepository(session).estadisticas_periodo(DEV, DESDE, HASTA, 2.0, 8.0)

    # Sincronizada + reconstruida dentro + manual. Fuera: la capturada antes
    # del periodo y la que no tiene hora de captura (contada aparte).
    assert agregados["total_lecturas"] == 3
    assert agregados["lecturas_sin_hora"] == 1
    assert agregados["temp_minima"] == pytest.approx(4.0)
    assert agregados["temp_maxima"] == pytest.approx(9.5)
    assert agregados["lecturas_en_rango"] == 2
    assert agregados["por_nivel"] == {"normal": 2, "excursion_critica": 1}


@pytest.mark.asyncio
async def test_resumen_del_pdf_y_curva_cuentan_igual_las_lecturas_del_nodo(db_session_factory):
    await _sembrar_mezcla(db_session_factory)
    async with db_session_factory() as session:
        repo = SQLAlchemyLecturaRepository(session)
        agregados = await repo.estadisticas_periodo(DEV, DESDE, HASTA, 2.0, 8.0)
        resumen = await repo.resumir_periodo(DEV, DESDE, HASTA)

    # La curva aparta las manuales; el resto coincide lectura a lectura.
    assert agregados["total_lecturas"] - resumen.lecturas_manuales == resumen.lecturas
    assert agregados["lecturas_sin_hora"] == resumen.lecturas_sin_hora
    assert agregados["temp_maxima"] == pytest.approx(resumen.temperatura_interna.maximo)


@pytest.mark.asyncio
async def test_sin_device_ni_cotas_el_resumen_sigue_funcionando(db_session_factory):
    await _sembrar_mezcla(db_session_factory)
    async with db_session_factory() as session:
        agregados = await SQLAlchemyLecturaRepository(session).estadisticas_periodo(None, None, None, 2.0, 8.0)
    # Todas con hora de captura (4) y una sin hora.
    assert agregados["total_lecturas"] == 4
    assert agregados["lecturas_sin_hora"] == 1


@pytest.mark.asyncio
async def test_la_serie_acota_la_llegada_con_la_antiguedad_maxima(db_session_factory):
    retraso = timedelta(hours=get_settings().lectura_antiguedad_max_horas)
    await _lecturas(
        db_session_factory,
        # Llegó dentro del margen: entra.
        {"timestamp": HASTA + retraso - timedelta(minutes=1), "timestamp_estimado": DESDE + timedelta(hours=2),
         "time_quality": "unsynced"},
        # Llegó más tarde que el margen: una lectura así se habría rechazado por
        # antigua (HU-07) si su hora se hubiera conocido al llegar.
        {"timestamp": HASTA + retraso + timedelta(minutes=1), "timestamp_estimado": DESDE + timedelta(hours=3),
         "time_quality": "unsynced"},
    )
    async with db_session_factory() as session:
        repo = SQLAlchemyLecturaRepository(session)
        buckets = await repo.agregar_serie(DEV, DESDE, HASTA, 3600)
        resumen = await repo.resumir_periodo(DEV, DESDE, HASTA)
    assert [b.inicio for b in buckets] == [DESDE + timedelta(hours=2)]
    assert resumen.lecturas == 1


def test_el_filtro_de_periodo_acota_timestamp_por_ambos_lados():
    repo = SQLAlchemyLecturaRepository(session=None, retraso_llegada_maximo=timedelta(hours=168))  # type: ignore[arg-type]
    filtros = [
        str(f.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for f in repo._en_periodo(DEV, DESDE, HASTA)
    ]
    assert any("thermal_readings.timestamp >=" in f and "2026-09-01" in f for f in filtros)
    # hasta (2026-09-02) + 168 h = 2026-09-09: cota superior que usa el índice.
    assert any("thermal_readings.timestamp <=" in f and "2026-09-09" in f for f in filtros)


def test_el_pdf_avisa_cuantas_lecturas_quedaron_fuera_por_no_tener_hora():
    base = {
        "total_lecturas": 3, "lecturas_con_temperatura": 3, "lecturas_en_rango": 2, "temp_minima": 4.0,
        "temp_maxima": 9.5, "temp_promedio": 6.0, "por_nivel": {"normal": 3},
    }
    generador = GeneradorReporteBPAPDF()

    def _texto(est: dict) -> str:
        salida = []
        for f in generador._seccion_resumen(est):
            salida.extend(getattr(c, "text", "") for fila in getattr(f, "_cellvalues", []) for c in fila)
            salida.append(getattr(f, "text", ""))
        return " ".join(salida)

    con_sin_hora = _texto(_estadisticas_de_agregados({**base, "lecturas_sin_hora": 2}))
    assert "2 lectura(s) del período llegaron sin hora de captura" in con_sin_hora
    # Repositorios previos sin el campo: sin aviso y sin error.
    assert "sin hora de captura" not in _texto(_estadisticas_de_agregados(base))


def test_el_pdf_no_muestra_identificadores_internos_de_historias():
    generador = GeneradorReporteBPAPDF()
    digest = "b" * 64
    textos = []
    for veredicto in (
        {"estado": "no_concluida", "integra": False, "total_no_verificables": 1, "verificado_en": "x",
         "digest_cadena": digest, "ultima_verificacion": None},
        {"estado": "ruptura_aislada", "integra": False, "total_rupturas": 1, "integra_desde": "x",
         "verificado_en": "x", "digest_cadena": digest},
        {"estado": "ruptura_detectada", "integra": False, "primer_registro_inconsistente": 3,
         "registros_posteriores_afectados": 2, "verificado_en": "x", "digest_cadena": digest,
         "ultima_verificacion": {"resultado": "integra", "realizada_en": "x", "device_id": DEV, "desde": "a",
                                 "hasta": "b", "total_bloques_verificados": 4, "hash_eslabon": digest}},
    ):
        for f in generador._seccion_integridad(veredicto):
            for fila in getattr(f, "_cellvalues", []):
                textos.extend(getattr(c, "text", str(c)) for c in fila)
    texto = " ".join(textos)
    assert "Última verificación por dispositivo y periodo:" in texto
    assert "(HU-" not in texto
