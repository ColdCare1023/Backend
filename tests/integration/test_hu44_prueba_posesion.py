"""HU-44: la retención por compromiso termina con una prueba de posesión, no
con la confirmación del administrador, y no deja de vigilar la temperatura.

Pruebas C (excursión durante la ventana), D (atacante con el token viejo) y
E (confirmar sin cambiar la consola) de la revisión del 27/10, a nivel de
backend. El banco con el nodo físico y EMQX está en
`docs/evidencias/HU-44_evidencia.md`."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.application.use_cases.prueba_posesion import clave_posesion, firma_esperada
from src.infrastructure.config import get_settings
from src.infrastructure.database.models import (
    AuditLogModel,
    DeviceModel,
    IncidenteOperativoModel,
    ThermalReadingModel,
)
from src.interface import main
from src.interface.api.dispositivos_router import get_broker_admin, get_publicador_desafios
from src.interface.tareas import VigilanteSinDatos
from tests.conftest import auth_header

DEVICE_ID = "FARM-HU44POP"


class _Espia:
    configurado = True

    def __init__(self) -> None:
        self.acuses: list[dict] = []
        self.desafios: list[dict] = []
        self.expulsados: list[str] = []

    async def publish(self, topic, payload=None, qos=0, **_kwargs):
        mensaje = json.loads(payload)
        (self.desafios if mensaje.get("tipo") == "desafio_posesion" else self.acuses).append(mensaje)

    async def publicar(self, device_id: str, mensaje: str) -> bool:
        self.desafios.append(json.loads(mensaje))
        return True

    async def aprovisionar_credencial(self, device_id: str, token: str):
        return None  # modo manual (EMQX Serverless)

    async def eliminar_credencial(self, device_id: str):
        return None

    async def desconectar_cliente(self, client_id: str) -> bool:
        self.expulsados.append(client_id)
        return True


class _Broadcaster:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict]] = []

    async def publicar(self, datos, tipo=None):
        self.eventos.append((tipo, datos))


class _Notificaciones:
    def __init__(self) -> None:
        self.avisos: list = []

    def encolar(self, aviso) -> bool:
        self.avisos.append(aviso)
        return True

    def encolar_todos(self, avisos) -> None:
        self.avisos.extend(avisos)

    async def iniciar(self) -> None:
        return None

    async def detener(self) -> None:
        return None


class _Mensaje:
    def __init__(self, sufijo: str, payload: dict) -> None:
        self.topic = f"farmacias/{DEVICE_ID}/{sufijo}"
        self.payload = json.dumps(payload).encode()


@pytest.fixture
def espia(app, monkeypatch, db_session_factory):
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    doble = _Espia()
    monkeypatch.setattr(main, "BrokerAdmin", lambda _settings: doble)
    app.dependency_overrides[get_broker_admin] = lambda: doble
    app.dependency_overrides[get_publicador_desafios] = lambda: doble
    yield doble
    get_settings.cache_clear()


def _alta(client, token_admin) -> None:
    cabecera = auth_header(token_admin)
    assert client.post("/api/dispositivos", json={"device_id": DEVICE_ID}, headers=cabecera).status_code == 201
    assert client.post(f"/api/dispositivos/{DEVICE_ID}/broker/confirmar", json={}, headers=cabecera).status_code == 200


def _rotar_por_compromiso(client, token_admin) -> str:
    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "token visto en un log", "causa": "compromiso"},
        headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 200 and respuesta.json()["telemetria_retenida"] is True
    return respuesta.json()["token"]


def _confirmar(client, token_admin) -> dict:
    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/broker/confirmar", json={"nota": "consola"}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 200
    return respuesta.json()


def _telemetria(reading_id: str, temperatura: float = 4.0) -> dict:
    return {
        "device_id": DEVICE_ID, "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "temperatura_interna": temperatura, "temperatura_ambiental": 20.0, "humedad_ambiental": 50.0,
        "apertura_refrigerador": False, "estado_conectividad": "online", "reading_id": reading_id,
    }


def _evento(event_type: str, detail: dict | None = None) -> dict:
    cuerpo = {
        "schema_version": "1.0", "device_id": DEVICE_ID, "event_type": event_type, "boot_id": 687,
        "time_quality": "sntp_synced",
    }
    if detail is not None:
        cuerpo["detail"] = detail
    return cuerpo


def _respuesta(token: str, nonce: str) -> dict:
    return _evento("prueba_posesion", {"nonce": nonce, "hmac": firma_esperada(clave_posesion(token), nonce, DEVICE_ID)})


async def _filas(db_session_factory, modelo, *condiciones):
    async with db_session_factory() as session:
        return (await session.execute(select(modelo).where(*condiciones))).scalars().all()


async def _acciones(db_session_factory) -> list[str]:
    return [a.accion for a in await _filas(db_session_factory, AuditLogModel)]


async def _incidentes_abiertos(db_session_factory) -> set[str]:
    filas = await _filas(
        db_session_factory, IncidenteOperativoModel,
        IncidenteOperativoModel.device_id == DEVICE_ID, IncidenteOperativoModel.resuelto_en.is_(None),
    )
    return {f.tipo for f in filas}


async def test_la_rotacion_por_compromiso_abre_monitoreo_suspendido_y_lo_avisa(
    client, token_admin, espia, db_session_factory, app
):
    notificaciones = _Notificaciones()
    app.state.notificaciones = notificaciones
    _alta(client, token_admin)
    _rotar_por_compromiso(client, token_admin)

    assert await _incidentes_abiertos(db_session_factory) == {"monitoreo_suspendido"}
    assert [a.tipo for a in notificaciones.avisos] == ["monitoreo_suspendido"]
    estado = client.get("/api/dispositivos/estado-operativo", headers=auth_header(token_admin)).json()
    nodo = next(e for e in estado if e["id"] == DEVICE_ID)
    assert nodo["estado_operativo"] == "MONITOREO_SUSPENDIDO"
    assert [i["tipo"] for i in nodo["incidencias_operativas"]] == ["monitoreo_suspendido"]


async def test_sin_datos_no_se_abre_durante_la_retencion(client, token_admin, espia, db_session_factory):
    _alta(client, token_admin)
    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("antes")), _Broadcaster(), espia)
    _rotar_por_compromiso(client, token_admin)

    vigilante = VigilanteSinDatos(db_session_factory, _Broadcaster(), None, 30, 3)
    await vigilante.ejecutar_una_vez(datetime.now(tz=timezone.utc) + timedelta(minutes=10))

    assert "sin_datos" not in await _incidentes_abiertos(db_session_factory)


async def test_c_excursion_en_lectura_retenida_alerta_con_origen_no_verificado(
    client, token_admin, espia, db_session_factory
):
    _alta(client, token_admin)
    _rotar_por_compromiso(client, token_admin)
    notificaciones, broadcaster = _Notificaciones(), _Broadcaster()

    for _ in range(3):  # el nodo reintenta la misma lectura: un solo episodio
        await main._procesar_lectura_mqtt(
            _Mensaje("lecturas", _telemetria("calor", 11.5)), broadcaster, espia, notificaciones
        )

    assert await _filas(db_session_factory, ThermalReadingModel) == []  # sigue sin persistirse
    incidente = (await _filas(
        db_session_factory, IncidenteOperativoModel, IncidenteOperativoModel.tipo == "excursion_no_verificada"
    ))[0]
    assert incidente.detalle["temperatura_interna"] == 11.5
    assert incidente.detalle["origen_verificado"] is False
    assert [a.tipo for a in notificaciones.avisos] == ["excursion_no_verificada"]
    assert ("excursion_no_verificada" in [t for t, _ in broadcaster.eventos])
    assert (await _acciones(db_session_factory)).count("EXCURSION_NO_VERIFICADA_DETECTADA") == 1


async def test_c_la_muestra_vigente_detecta_la_excursion_aunque_la_cola_este_detenida(
    client, token_admin, espia, db_session_factory
):
    _alta(client, token_admin)
    _rotar_por_compromiso(client, token_admin)
    notificaciones = _Notificaciones()
    captura = datetime.now(tz=timezone.utc).isoformat()

    await main._procesar_evento_mqtt(
        _Mensaje("eventos", _evento("lectura_vigente", {"seq_no": 95, "temperatura_interna": 1.2, "captured_at": captura})),
        _Broadcaster(), espia, notificaciones,
    )
    # Un sensor en falla nunca dispara la regla (P3).
    await main._procesar_evento_mqtt(
        _Mensaje("eventos", _evento("lectura_vigente", {"seq_no": 96, "temperatura_interna": None})),
        _Broadcaster(), espia, notificaciones,
    )

    assert "excursion_no_verificada" in await _incidentes_abiertos(db_session_factory)
    assert len(notificaciones.avisos) == 1


async def test_la_muestra_vigente_se_ignora_fuera_de_la_retencion(client, token_admin, espia, db_session_factory):
    _alta(client, token_admin)
    await main._procesar_evento_mqtt(
        _Mensaje("eventos", _evento("lectura_vigente", {"seq_no": 5, "temperatura_interna": 12.0})), _Broadcaster(), espia
    )
    assert await _incidentes_abiertos(db_session_factory) == set()


async def test_prueba_de_posesion_valida_termina_la_retencion(client, token_admin, espia, db_session_factory):
    _alta(client, token_admin)
    token = _rotar_por_compromiso(client, token_admin)
    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("r1", 11.0)), _Broadcaster(), espia)
    _confirmar(client, token_admin)
    nonce = espia.desafios[-1]["nonce"]
    broadcaster = _Broadcaster()

    await main._procesar_evento_mqtt(_Mensaje("eventos", _respuesta(token, nonce)), broadcaster, espia)
    # El nodo reenvía desde LittleFS lo retenido: ahora sí se persiste y acusa.
    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("r1", 11.0)), _Broadcaster(), espia)

    dispositivo = (await _filas(db_session_factory, DeviceModel, DeviceModel.id == DEVICE_ID))[0]
    assert dispositivo.rotacion_por_compromiso is False and dispositivo.pop_nonce is None
    assert len(await _filas(db_session_factory, ThermalReadingModel)) == 1
    assert espia.acuses[-1]["estado"] == "commit_confirmado"
    assert await _incidentes_abiertos(db_session_factory) == set()
    acciones = await _acciones(db_session_factory)
    assert "CREDENCIAL_POSESION_VERIFICADA" in acciones
    assert {"MONITOREO_SUSPENDIDO_RESUELTO", "EXCURSION_NO_VERIFICADA_RESUELTO"} <= set(acciones)
    assert ("prueba_posesion", {"device_id": DEVICE_ID, "verificada": True, "motivo": "verificada"}) in broadcaster.eventos


async def test_e_confirmar_sin_cambiar_la_consola_no_sale_de_la_retencion(
    client, token_admin, espia, db_session_factory
):
    """El broker rechazó el relevo (la contraseña no cambió) y el nodo volvió
    con el token anterior: firma con él y la retención sigue."""
    _alta(client, token_admin)
    _rotar_por_compromiso(client, token_admin)
    _confirmar(client, token_admin)
    nonce = espia.desafios[-1]["nonce"]

    await main._procesar_evento_mqtt(_Mensaje("eventos", _respuesta("token-anterior-filtrado", nonce)), _Broadcaster(), espia)
    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("e1")), _Broadcaster(), espia)

    assert await _filas(db_session_factory, ThermalReadingModel) == []
    falla = [a for a in await _filas(db_session_factory, AuditLogModel) if a.accion == "PRUEBA_POSESION_FALLIDA"]
    assert falla and falla[0].detalle["motivo"] == "firma_invalida"


async def test_d_el_atacante_no_puede_reutilizar_ni_inventar_un_nonce(client, token_admin, espia, db_session_factory):
    _alta(client, token_admin)
    token = _rotar_por_compromiso(client, token_admin)
    # Antes de confirmar la consola no hay prueba posible.
    await main._procesar_evento_mqtt(_Mensaje("eventos", _respuesta(token, "ab" * 16)), _Broadcaster(), espia)
    _confirmar(client, token_admin)
    nonce = espia.desafios[-1]["nonce"]
    # Un nonce que el backend no emitió.
    await main._procesar_evento_mqtt(_Mensaje("eventos", _respuesta(token, "cd" * 16)), _Broadcaster(), espia)

    motivos = [a.detalle["motivo"] for a in await _filas(db_session_factory, AuditLogModel)
               if a.accion == "PRUEBA_POSESION_FALLIDA"]
    assert motivos == ["consola_pendiente", "nonce_desconocido"]

    await main._procesar_evento_mqtt(_Mensaje("eventos", _respuesta(token, nonce)), _Broadcaster(), espia)
    # El mismo nonce ya no sirve para una segunda rotación ni para repetir la respuesta.
    dispositivo = (await _filas(db_session_factory, DeviceModel, DeviceModel.id == DEVICE_ID))[0]
    assert dispositivo.rotacion_por_compromiso is False and dispositivo.pop_nonce is None


async def test_el_nonce_caduca(client, token_admin, espia, db_session_factory):
    _alta(client, token_admin)
    token = _rotar_por_compromiso(client, token_admin)
    _confirmar(client, token_admin)
    nonce = espia.desafios[-1]["nonce"]
    async with db_session_factory() as session:
        dispositivo = await session.get(DeviceModel, DEVICE_ID)
        dispositivo.pop_nonce_emitido_en = datetime.now(tz=timezone.utc) - timedelta(minutes=11)
        await session.commit()

    await main._procesar_evento_mqtt(_Mensaje("eventos", _respuesta(token, nonce)), _Broadcaster(), espia)

    motivos = [a.detalle["motivo"] for a in await _filas(db_session_factory, AuditLogModel)
               if a.accion == "PRUEBA_POSESION_FALLIDA"]
    assert motivos == ["nonce_caducado"]


async def test_una_sesion_nueva_recibe_un_desafio_nuevo(client, token_admin, espia, db_session_factory):
    """El desafío enviado al confirmar se pierde con la sesión expulsada: el
    `online` de la reconexión dispara otro, sin esperar la reemisión mínima."""
    _alta(client, token_admin)
    _rotar_por_compromiso(client, token_admin)
    _confirmar(client, token_admin)
    primero = espia.desafios[-1]["nonce"]

    await main._procesar_evento_mqtt(_Mensaje("eventos", _evento("online", {"rssi_dbm": -60})), _Broadcaster(), espia)
    # Una lectura retenida inmediatamente después no vuelve a emitir (≥ 30 s).
    await main._procesar_lectura_mqtt(_Mensaje("lecturas", _telemetria("s1")), _Broadcaster(), espia)

    assert len(espia.desafios) == 2 and espia.desafios[-1]["nonce"] != primero


async def test_el_par_lwt_online_de_una_rotacion_se_audita_como_reconexion(
    client, token_admin, espia, db_session_factory
):
    """El LWT y el `online` que siguen a la expulsión de la sesión no son una
    caída del nodo: la bitácora los marca como reconexión por rotación."""
    _alta(client, token_admin)
    await main._procesar_evento_mqtt(_Mensaje("eventos", _evento("online", {"rssi_dbm": -60})), _Broadcaster(), espia)
    respuesta = client.post(
        f"/api/dispositivos/{DEVICE_ID}/credenciales/rotar",
        json={"motivo": "rotación programada", "causa": "preventiva"}, headers=auth_header(token_admin),
    )
    assert respuesta.status_code == 200

    lwt = {"schema_version": "1.0", "device_id": DEVICE_ID, "event_type": "lwt_offline", "boot_id": 687}
    await main._procesar_evento_mqtt(_Mensaje("eventos", lwt), _Broadcaster(), espia)
    await main._procesar_evento_mqtt(_Mensaje("eventos", _evento("online", {"rssi_dbm": -60})), _Broadcaster(), espia)

    filas = [a for a in await _filas(db_session_factory, AuditLogModel)
             if a.accion in ("DISPOSITIVO_OFFLINE", "DISPOSITIVO_ONLINE")]
    motivos = [(a.accion, (a.detalle or {}).get("motivo")) for a in filas]
    # El alta también confirma la consola y expulsa la sesión.
    assert motivos[0] == ("DISPOSITIVO_ONLINE", "reconexion_por_confirmacion_consola")
    assert motivos[-2:] == [("DISPOSITIVO_OFFLINE", "reconexion_por_rotacion"),
                            ("DISPOSITIVO_ONLINE", "reconexion_por_rotacion")]
