"""HU-31 / HU-36: GET /api/lecturas/serie dibuja el periodo COMPLETO.

Antes la curva pedía como mucho 5000 lecturas y el Historial 200: a 10 s por
lectura eso cubría unas 14 h y 33 min. Aquí 24 h a 10 s y 7 días a 30 s deben
llegar enteros (agregados por intervalo), con las brechas y el resumen del
periodo, y GET /api/lecturas informa el total sin paginar (X-Total-Count)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import insert

from src.infrastructure.database.models import DeviceModel, EpisodioBufferSaturadoModel, ThermalReadingModel
from tests.conftest import auth_header

URL = "/api/lecturas/serie"
DEV = "ESP32-SERIE-01"


def _dia_alineado(dias_atras: int) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(days=dias_atras)).replace(hour=0, minute=0, second=0, microsecond=0)


async def _sembrar(factory, inicio: datetime, n: int, paso_s: int, device_id: str = DEV, **columnas) -> None:
    """Inserta `n` lecturas del nodo cada `paso_s` segundos desde `inicio`."""
    async with factory() as session:
        if await session.get(DeviceModel, device_id) is None:
            session.add(DeviceModel(id=device_id, created_at=inicio - timedelta(days=30)))
            await session.flush()
        filas = [
            {
                "device_id": device_id, "timestamp": inicio + timedelta(seconds=i * paso_s),
                "received_at": inicio + timedelta(seconds=i * paso_s + 1), "temperatura_interna": 5.0,
                "temperatura_ambiental": 21.0, "humedad_ambiental": 55.0, "apertura_refrigerador": False,
                "estado_conectividad": "online", "riesgo_efectivo": "normal", "time_quality": "synced",
                "boot_id": 1, "seq_no": i + 1, **columnas,
            }
            for i in range(n)
        ]
        if filas:
            await session.execute(insert(ThermalReadingModel), filas)
        await session.commit()


async def _lectura(factory, **columnas) -> None:
    async with factory() as session:
        session.add(ThermalReadingModel(**{
            "device_id": DEV, "temperatura_interna": 5.0, "estado_conectividad": "online",
            "apertura_refrigerador": False, **columnas,
        }))
        await session.commit()


def _serie(client, token, **params):
    return client.get(URL, params={"device_id": DEV, **params}, headers=auth_header(token))


@pytest.mark.asyncio
async def test_24_horas_a_10_segundos_se_dibujan_completas(client, token_tecnico, db_session_factory):
    desde = _dia_alineado(3)
    await _sembrar(db_session_factory, desde, 8640, 10)

    r = _serie(client, token_tecnico, desde=desde.isoformat(), hasta=(desde + timedelta(hours=24)).isoformat())

    assert r.status_code == 200
    serie = r.json()
    assert serie["bucket_segundos"] == 60 and serie["bucket_automatico"] is True
    assert serie["total_lecturas"] == 8640
    assert len(serie["buckets"]) == 1440
    assert sum(b["lecturas"] for b in serie["buckets"]) == 8640
    assert serie["buckets"][0]["inicio"].startswith(desde.strftime("%Y-%m-%dT%H:%M:%S"))
    assert serie["buckets"][-1]["fin"].startswith((desde + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"))
    assert serie["brechas"] == []
    assert serie["resumen"]["primera_lectura_en"].startswith(desde.strftime("%Y-%m-%dT%H:%M:%S"))
    assert serie["resumen"]["por_riesgo_efectivo"] == {"normal": 8640}


@pytest.mark.asyncio
async def test_7_dias_a_30_segundos_se_dibujan_completos(client, token_farmaceutico, db_session_factory):
    desde = _dia_alineado(9)
    await _sembrar(db_session_factory, desde, 20160, 30)

    r = _serie(client, token_farmaceutico, desde=desde.isoformat(), hasta=(desde + timedelta(days=7)).isoformat())

    serie = r.json()
    assert r.status_code == 200
    assert serie["bucket_segundos"] == 600
    assert serie["total_lecturas"] == 20160
    assert len(serie["buckets"]) == 1008
    assert {b["lecturas"] for b in serie["buckets"]} == {20}
    assert serie["brechas"] == []


@pytest.mark.asyncio
async def test_el_maximo_del_intervalo_conserva_una_excursion_breve(client, token_tecnico, db_session_factory):
    """HU-31 esc. 3: la agregación no pierde la forma real de la tendencia."""
    desde = _dia_alineado(2)
    await _sembrar(db_session_factory, desde, 60, 10)
    await _lectura(db_session_factory, timestamp=desde + timedelta(seconds=125), temperatura_interna=12.5,
                   excursion_confirmada=True, riesgo_efectivo="excursion_critica", time_quality="synced",
                   boot_id=1, seq_no=999)

    serie = _serie(client, token_tecnico, desde=desde.isoformat(), hasta=(desde + timedelta(minutes=10)).isoformat(),
                   bucket="60").json()

    tercero = serie["buckets"][2]
    assert tercero["temperatura_interna"] == {"min": 5.0, "max": 12.5, "promedio": pytest.approx(6.071, abs=1e-3),
                                              "n": 7}
    assert (tercero["fuera_de_rango"], tercero["excursiones"]) == (1, 1)
    assert serie["resumen"]["temperatura_interna"]["max"] == 12.5
    assert serie["resumen"]["fuera_de_rango"] == 1
    assert serie["resumen"]["por_riesgo_efectivo"] == {"normal": 60, "excursion_critica": 1}


@pytest.mark.asyncio
async def test_un_hueco_se_informa_como_brecha_y_con_su_causa(client, token_auditor, db_session_factory):
    desde = _dia_alineado(2)
    await _sembrar(db_session_factory, desde, 40, 30)  # 0:00 a 0:19:30
    reanuda = desde + timedelta(minutes=40)
    await _sembrar(db_session_factory, reanuda, 40, 30, boot_id=2)  # 0:40 a 0:59:30
    hasta = desde + timedelta(hours=1)

    serie = _serie(client, token_auditor, desde=desde.isoformat(), hasta=hasta.isoformat()).json()

    assert serie["umbral_brecha_segundos"] == 90
    [brecha] = serie["brechas"]
    assert brecha["desde"].startswith((desde + timedelta(minutes=19, seconds=30)).strftime("%Y-%m-%dT%H:%M:%S"))
    assert brecha["hasta"].startswith(reanuda.strftime("%Y-%m-%dT%H:%M:%S"))
    assert (brecha["duracion_segundos"], brecha["causa"], brecha["abierta"]) == (1230, "sin_datos", False)

    # HU-48 esc. 2: si el buffer del nodo se agotó en ese tramo, la causa se conoce.
    async with db_session_factory() as session:
        session.add(EpisodioBufferSaturadoModel(
            id=uuid4(), device_id=DEV, desde=desde + timedelta(minutes=20), hasta=desde + timedelta(minutes=39),
            desde_boot_id=1, desde_seq_no=41, causa="buffer_lleno", perdidas=38, abierto=False,
            registrado_en=reanuda, actualizado_en=reanuda,
        ))
        await session.commit()
    serie = _serie(client, token_auditor, desde=desde.isoformat(), hasta=hasta.isoformat()).json()
    assert (serie["brechas"][0]["causa"], serie["brechas"][0]["perdidas"]) == ("buffer_agotado", 38)


@pytest.mark.asyncio
async def test_brecha_en_curso_hasta_ahora(client, token_tecnico, db_session_factory):
    ahora = datetime.now(timezone.utc)
    await _sembrar(db_session_factory, ahora - timedelta(hours=1), 100, 30)  # termina hace ~10 min

    serie = _serie(client, token_tecnico).json()

    assert serie["bucket_segundos"] == 60  # sin rango: últimas 24 h (HU-31 esc. 4)
    assert serie["total_lecturas"] == 100
    ultima = serie["brechas"][-1]
    assert ultima["abierta"] is True and ultima["causa"] == "sin_datos"
    assert 590 <= ultima["duracion_segundos"] <= 700


@pytest.mark.asyncio
async def test_lecturas_sin_hora_y_manuales_no_entran_en_la_curva(client, token_tecnico, db_session_factory):
    """HU-07: una lectura `unsynced` sin reanclar no tiene hora de captura;
    una reanclada se ubica en su hora reconstruida, aunque llegara después."""
    desde = _dia_alineado(2)
    await _sembrar(db_session_factory, desde, 10, 30)
    hasta = desde + timedelta(hours=1)
    await _lectura(db_session_factory, timestamp=desde + timedelta(minutes=30), time_quality="unsynced",
                   boot_id=9, seq_no=1, temperatura_interna=30.0)
    await _lectura(db_session_factory, timestamp=desde + timedelta(hours=5), time_quality="unsynced", boot_id=9,
                   seq_no=2, timestamp_estimado=desde + timedelta(minutes=50), temperatura_interna=6.5)
    await _lectura(db_session_factory, timestamp=desde + timedelta(minutes=45), origen="manual",
                   temperatura_interna=-3.0)

    serie = _serie(client, token_tecnico, desde=desde.isoformat(), hasta=hasta.isoformat(), bucket="600").json()

    assert serie["total_lecturas"] == 11
    assert (serie["lecturas_sin_hora"], serie["lecturas_manuales"]) == (1, 1)
    assert serie["resumen"]["temperatura_interna"]["max"] == 6.5
    assert serie["resumen"]["temperatura_interna"]["min"] == 5.0
    assert serie["buckets"][-1]["inicio"].startswith((desde + timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M"))


def test_sin_lecturas_devuelve_la_serie_vacia(client, token_tecnico, db_session_factory):
    """HU-31 esc. 2 / HU-36 esc. 2: estado vacío, no un error."""
    import asyncio

    async def _alta():
        async with db_session_factory() as session:
            session.add(DeviceModel(id=DEV))
            await session.commit()

    asyncio.run(_alta())
    serie = _serie(client, token_tecnico).json()
    assert (serie["total_lecturas"], serie["buckets"], serie["brechas"]) == (0, [], [])
    assert serie["resumen"]["temperatura_interna"] == {"min": None, "max": None, "promedio": None, "n": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "detalle"),
    [
        ({"desde": "2026-11-10T00:00:00Z", "hasta": "2026-11-09T00:00:00Z"}, "desde no puede ser posterior a hasta"),
        ({"desde": "2024-01-01T00:00:00Z", "hasta": "2026-01-01T00:00:00Z"}, "366 días"),
        ({"bucket": "5"}, "entre 10 y 86400"),
        ({"bucket": "10"}, "más de 5000 puntos"),
    ],
)
async def test_rangos_e_intervalos_invalidos_se_rechazan(client, token_tecnico, db_session_factory, params, detalle):
    await _sembrar(db_session_factory, _dia_alineado(1), 1, 30)
    r = _serie(client, token_tecnico, **params)
    assert r.status_code == 422
    assert detalle in r.json()["detail"]


@pytest.mark.parametrize("params", [{"bucket": "rapido"}, {"device_id": "no válido!"}])
def test_parametros_mal_formados_son_422(client, token_tecnico, params):
    assert client.get(URL, params={"device_id": DEV, **params}, headers=auth_header(token_tecnico)).status_code == 422


def test_dispositivo_inexistente_es_404(client, token_tecnico):
    assert _serie(client, token_tecnico).status_code == 404


def test_exige_autenticacion(client):
    assert client.get(URL, params={"device_id": DEV}).status_code == 401


@pytest.mark.asyncio
async def test_mismos_roles_que_el_historial(client, token_tecnico, token_farmaceutico, token_auditor, token_admin,
                                             db_session_factory):
    await _sembrar(db_session_factory, _dia_alineado(1), 1, 30)
    for token in (token_tecnico, token_farmaceutico, token_auditor, token_admin):
        assert _serie(client, token).status_code == 200


@pytest.mark.asyncio
async def test_historial_informa_el_total_sin_paginar(client, token_farmaceutico, db_session_factory):
    """La tabla del Historial pagina con offset y avisa si no muestra todo."""
    desde = _dia_alineado(1)
    await _sembrar(db_session_factory, desde, 7, 30)

    def _pagina(**params):
        return client.get("/api/lecturas", params={"device_id": DEV, **params}, headers=auth_header(token_farmaceutico))

    recortada = _pagina(limite=3)
    assert len(recortada.json()) == 3 and recortada.headers["X-Total-Count"] == "7"
    assert _pagina(limite=100).headers["X-Total-Count"] == "7"
    assert _pagina(limite=3, offset=6).headers["X-Total-Count"] == "7"
    assert _pagina(limite=3, offset=50).headers["X-Total-Count"] == "7"
    filtrada = _pagina(limite=2, desde=(desde + timedelta(seconds=60)).isoformat())
    assert filtrada.headers["X-Total-Count"] == "5"


def test_cors_expone_el_total_al_frontend(client):
    r = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert "X-Total-Count" in r.headers.get("access-control-expose-headers", "")
