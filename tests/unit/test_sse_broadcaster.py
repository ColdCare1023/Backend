"""RF-11: difusión SSE con clientes lentos y desconexiones.

Se prueba a nivel de unidad y no por HTTP a propósito: `TestClient` consume el
`StreamingResponse` de forma síncrona y se bloquea indefinidamente contra un
stream infinito, así que la cobertura de estos caminos tiene que venir de
ejercitar el broadcaster y el generador directamente.
"""

import asyncio
import json
import time

import pytest

from src.infrastructure.security.revocation_store import JtiStore
from src.interface.api.sse_broadcaster import SSEBroadcaster
from src.interface.api.sse_router import _event_stream


@pytest.mark.asyncio
async def test_el_evento_llega_a_todos_los_suscriptores():
    broadcaster = SSEBroadcaster()
    cola_a, cola_b = broadcaster.subscribe(), broadcaster.subscribe()

    await broadcaster.publicar({"device_id": "ESP32-01", "temperatura_interna": 5.2}, "lectura")

    for cola in (cola_a, cola_b):
        mensaje = cola.get_nowait()
        assert mensaje.startswith("event: lectura\ndata: ")
        assert json.loads(mensaje.split("data: ", 1)[1])["device_id"] == "ESP32-01"


@pytest.mark.asyncio
async def test_el_tipo_de_evento_viaja_en_el_campo_event():
    """El cliente distingue alerta de lectura por el campo `event`, no
    interpretando el texto del payload."""
    broadcaster = SSEBroadcaster()
    cola = broadcaster.subscribe()

    await broadcaster.publicar({"id": 1}, "excursion_critica")

    assert cola.get_nowait().startswith("event: excursion_critica\n")


@pytest.mark.asyncio
async def test_un_cliente_lento_no_bloquea_al_resto():
    """Si una cola está llena, se descarta el evento PARA ESE cliente. Sin
    esto, `queue.put` esperaría y un navegador que dejó de leer congelaría la
    difusión para todos los demás y para el propio pipeline de ingesta."""
    broadcaster = SSEBroadcaster()
    lento = broadcaster.subscribe()
    rapido = broadcaster.subscribe()

    for _ in range(lento.maxsize):  # se satura solo el lento
        lento.put_nowait("relleno")

    await asyncio.wait_for(broadcaster.publicar({"n": 1}, "lectura"), timeout=1.0)

    assert lento.full()
    assert rapido.qsize() == 1


@pytest.mark.asyncio
async def test_desuscribir_deja_de_recibir_eventos():
    broadcaster = SSEBroadcaster()
    cola = broadcaster.subscribe()
    broadcaster.unsubscribe(cola)

    await broadcaster.publicar({"n": 1}, "lectura")

    assert cola.empty()


@pytest.mark.asyncio
async def test_desuscribir_dos_veces_no_falla():
    """La limpieza del generador SSE puede ejecutarse más de una vez ante una
    desconexión abrupta; no debe lanzar KeyError."""
    broadcaster = SSEBroadcaster()
    cola = broadcaster.subscribe()

    broadcaster.unsubscribe(cola)
    broadcaster.unsubscribe(cola)


class _RequestFalsa:
    """Request mínima: broadcaster, store de revocación y control de desconexión."""

    def __init__(self, broadcaster, desconectar_tras: int, revocacion=None):
        estado = type(
            "S",
            (),
            {
                "sse_broadcaster": broadcaster,
                "token_revocation": revocacion or JtiStore(100),
            },
        )()
        self.app = type("App", (), {"state": estado})()
        self._restantes = desconectar_tras

    async def is_disconnected(self) -> bool:
        self._restantes -= 1
        return self._restantes < 0


@pytest.mark.asyncio
async def test_el_stream_emite_el_comentario_inicial_y_luego_los_eventos():
    broadcaster = SSEBroadcaster()
    request = _RequestFalsa(broadcaster, desconectar_tras=2)

    generador = _event_stream(request)
    assert await generador.__anext__() == ": connected\n\n"

    await broadcaster.publicar({"device_id": "ESP32-01"}, "lectura")
    trozo = await generador.__anext__()
    assert trozo.startswith("event: lectura\n")
    assert trozo.endswith("\n\n")

    await generador.aclose()


@pytest.mark.asyncio
async def test_al_desconectarse_el_cliente_se_libera_su_suscripcion():
    """Sin esta liberación, cada recarga del dashboard dejaría una cola de 100
    mensajes viva para siempre: fuga de memoria en una instancia de 512 MB."""
    broadcaster = SSEBroadcaster()
    request = _RequestFalsa(broadcaster, desconectar_tras=0)

    trozos = [trozo async for trozo in _event_stream(request)]

    assert trozos == [": connected\n\n"]
    assert broadcaster._subscribers == set()


@pytest.mark.asyncio
async def test_cerrar_sesion_corta_el_stream_abierto():
    """Un stream SSE vive horas; la revocación solo se comprobaba al abrirlo.
    Sin esto, cerrar sesión dejaba el caudal de datos térmicos fluyendo hasta
    que el navegador se cerrara, lo que contradice la revocación de JWT que el
    sistema presenta como control de seguridad (RF-17)."""
    broadcaster = SSEBroadcaster()
    revocacion = JtiStore(100)
    jti_sesion = "jti-de-la-sesion"
    # Muchas vueltas disponibles: quien debe cortar es la revocación, no el
    # contador de desconexión del cliente.
    request = _RequestFalsa(broadcaster, desconectar_tras=50, revocacion=revocacion)

    generador = _event_stream(request, jti_sesion)
    assert await generador.__anext__() == ": connected\n\n"

    await broadcaster.publicar({"n": 1}, "lectura")
    assert (await generador.__anext__()).startswith("event: lectura\n")

    # Logout: el access token que pidió el ticket queda revocado.
    revocacion.registrar(jti_sesion, time.time() + 3600)
    await broadcaster.publicar({"n": 2}, "lectura")

    with pytest.raises(StopAsyncIteration):
        await generador.__anext__()

    assert broadcaster._subscribers == set(), "la suscripción debe liberarse al cortar"


@pytest.mark.asyncio
async def test_un_stream_sin_sesion_asociada_no_se_corta():
    """Compatibilidad: un ticket emitido antes de este cambio no lleva `ptk`.
    No debe cerrarse el stream por no poder comprobar la sesión."""
    broadcaster = SSEBroadcaster()
    request = _RequestFalsa(broadcaster, desconectar_tras=3)

    generador = _event_stream(request, None)
    assert await generador.__anext__() == ": connected\n\n"

    await broadcaster.publicar({"n": 1}, "lectura")
    assert (await generador.__anext__()).startswith("event: lectura\n")

    await generador.aclose()
