"""HU-26: alcance según la versión de la cadena, estado `no_concluida` sin la
clave HMAC (sin falsa alarma de corrupción) y verificación por `chain_id`.

Escenario 1: "si la clave de la v3 no está disponible, la verificación se
informa como no concluida y no como íntegra". Escenario 4: el resultado
declara qué comprueba según el mecanismo (v1/v2 sin clave, v3 con HMAC)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.application.use_cases.verificar_integridad_registro import (
    ALCANCE_HMAC,
    ALCANCE_SIN_CLAVE,
    alcance_para_versiones,
)
from src.infrastructure.config import get_settings
from src.infrastructure.database.models import TraceabilityRecordModel
from tests.conftest import auth_header

DEVICE = "ESP32-HU26-ALC"
CLAVE = "clave-hmac-hu26-para-pruebas-0000000000"


@pytest.fixture
def con_clave(monkeypatch):
    monkeypatch.setattr(get_settings(), "trazabilidad_hmac_key", CLAVE)


def _sin_clave(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "trazabilidad_hmac_key", None)


def _ingestar(client, token, n: int = 1, device: str = DEVICE) -> None:
    for _ in range(n):
        respuesta = client.post(
            "/api/lecturas",
            json={
                "device_id": device,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "temperatura_interna": 4.0,
                "temperatura_ambiental": 22.0,
                "humedad_ambiental": 40.0,
                "apertura_refrigerador": False,
                "estado_conectividad": "online",
            },
            headers=auth_header(token),
        )
        assert respuesta.status_code == 201, respuesta.text


def _verificar(client, token, chain_id: str | None = DEVICE):
    params = {"chain_id": chain_id} if chain_id else {}
    return client.get("/api/trazabilidad/verificar", params=params, headers=auth_header(token))


# ── Escenario 4: alcance por versión ──────────────────────────────────────


def test_alcance_reproduce_el_criterio_y_no_certifica():
    texto = alcance_para_versiones([2, 3])
    assert "comprueba la consistencia" in texto
    assert "no equivale a inmutabilidad" in texto
    assert "no certifica por sí mismo toda la historia del dato" in texto
    assert "DIGEMID" in texto
    # El texto anterior afirmaba más de lo que la verificación puede probar.
    assert "certifica únicamente que los registros almacenados no fueron alterados" not in texto


def test_alcance_solo_menciona_los_mecanismos_presentes():
    assert ALCANCE_SIN_CLAVE in alcance_para_versiones([1, 2])
    assert ALCANCE_HMAC not in alcance_para_versiones([1, 2])
    assert ALCANCE_HMAC in alcance_para_versiones([3])
    assert ALCANCE_SIN_CLAVE not in alcance_para_versiones([3])
    # Sin eslabones se describen ambos mecanismos.
    assert ALCANCE_SIN_CLAVE in alcance_para_versiones([]) and ALCANCE_HMAC in alcance_para_versiones([])


def test_cadena_v2_declara_el_limite_de_sha256_sin_clave(client, token_tecnico):
    _ingestar(client, token_tecnico, 2)
    cuerpo = _verificar(client, token_tecnico).json()

    assert cuerpo["versiones_hash"] == [2]
    assert ALCANCE_SIN_CLAVE in cuerpo["alcance"]
    assert ALCANCE_HMAC not in cuerpo["alcance"]


def test_cadena_v3_declara_que_depende_de_la_clave(client, token_tecnico, con_clave):
    _ingestar(client, token_tecnico, 2)
    cuerpo = _verificar(client, token_tecnico).json()

    assert cuerpo["estado"] == "integra"
    assert cuerpo["versiones_hash"] == [3]
    assert ALCANCE_HMAC in cuerpo["alcance"]
    assert ALCANCE_SIN_CLAVE not in cuerpo["alcance"]


# ── Escenario 1: sin clave, la verificación no concluye ───────────────────


def test_sin_clave_v3_la_verificacion_no_concluye_y_no_da_falsa_alarma(
    client, token_tecnico, monkeypatch, con_clave
):
    _ingestar(client, token_tecnico, 3)
    _sin_clave(monkeypatch)

    respuesta = _verificar(client, token_tecnico)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()

    assert cuerpo["estado"] == "no_concluida"
    assert cuerpo["integra"] is False  # nunca "íntegra" sin haber verificado el sello
    assert cuerpo["total_rupturas"] == 0
    assert cuerpo["rupturas"] == []
    assert cuerpo["total_no_verificables"] == 3
    assert {d["tipo_discrepancia"] for d in cuerpo["no_verificables"]} == {"no_verificable"}
    assert cuerpo["detalle_inconsistencia"]["tipo_discrepancia"] == "no_verificable"

    # Sin falsa alarma: ni bandera global, ni evento de emergencia.
    estado = client.get("/api/trazabilidad/estado", headers=auth_header(token_tecnico)).json()
    assert estado["cadena_comprometida"] is False
    eventos = client.get(
        "/api/trazabilidad",
        params={"tipo_evento": "CORRUPCION_CADENA_DETECTADA"},
        headers=auth_header(token_tecnico),
    ).json()
    assert eventos == []


async def test_sin_clave_un_enlace_roto_si_se_detecta(
    client, token_tecnico, db_session_factory, monkeypatch, con_clave
):
    """El enlace previous_hash → hash almacenado no depende de la clave."""
    _ingestar(client, token_tecnico, 3)
    async with db_session_factory() as session:
        filas = (
            await session.execute(
                select(TraceabilityRecordModel)
                .where(TraceabilityRecordModel.chain_id == DEVICE)
                .order_by(TraceabilityRecordModel.chain_seq)
            )
        ).scalars().all()
        roto = filas[1]
        roto.previous_hash = "f" * 64
        roto_id = str(roto.id)
        await session.commit()
    _sin_clave(monkeypatch)

    cuerpo = _verificar(client, token_tecnico).json()

    assert cuerpo["estado"] == "ruptura_detectada"
    assert cuerpo["rupturas"][0]["id"] == roto_id
    assert cuerpo["rupturas"][0]["tipo_discrepancia"] == "enlace_roto"
    assert cuerpo["total_no_verificables"] == 2


def test_no_se_puede_aislar_lo_que_no_concluyo(client, token_tecnico, token_admin, monkeypatch, con_clave):
    _ingestar(client, token_tecnico, 2)
    _sin_clave(monkeypatch)
    cuerpo = _verificar(client, token_tecnico).json()
    registro = cuerpo["detalle_inconsistencia"]["id"]

    respuesta = client.post(f"/api/trazabilidad/corrupcion/{registro}/aislar", headers=auth_header(token_admin))

    assert respuesta.status_code == 409
    assert "no concluyó" in respuesta.json()["detail"]


# ── Escenario 1: verificación por chain_id ────────────────────────────────


def test_chain_id_se_devuelve_con_el_hash_final_completo(client, token_tecnico):
    _ingestar(client, token_tecnico, 2)
    cuerpo = _verificar(client, token_tecnico).json()

    assert cuerpo["chain_id"] == DEVICE
    assert cuerpo["total_registros"] == 2
    assert len(cuerpo["hash_final"]) == 64
    int(cuerpo["hash_final"], 16)


def test_chain_id_inexistente_es_404_y_mal_formado_422(client, token_tecnico):
    assert _verificar(client, token_tecnico, "NO-EXISTE-HU26").status_code == 404
    assert _verificar(client, token_tecnico, "a/b").status_code == 422


def test_listar_cadenas_para_elegir_chain_id(client, token_tecnico):
    _ingestar(client, token_tecnico, 2)
    _ingestar(client, token_tecnico, 1, device="ESP32-HU26-OTRO")

    respuesta = client.get("/api/trazabilidad/cadenas", headers=auth_header(token_tecnico))

    assert respuesta.status_code == 200
    por_cadena = {c["chain_id"]: c for c in respuesta.json()}
    assert por_cadena[DEVICE]["total_registros"] == 2
    assert por_cadena[DEVICE]["es_sistema"] is False
    assert por_cadena["ESP32-HU26-OTRO"]["total_registros"] == 1
    # La bitácora (login, lecturas manuales) se encadena en SISTEMA.
    assert por_cadena["SISTEMA"]["es_sistema"] is True
    assert por_cadena["SISTEMA"]["ultimo_registro_en"] is not None
    # Cada cadena listada se puede verificar por su chain_id.
    assert _verificar(client, token_tecnico, "SISTEMA").json()["chain_id"] == "SISTEMA"
