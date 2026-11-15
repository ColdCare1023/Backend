"""HU-21 esc. 3 y HU-23 esc. 3 (P0-1 de la validación final del backlog).

Una alerta crítica ATENDIDA cerró su ciclo: alguien la reconoció y registró la
acción correctiva. Si la temperatura vuelve al rango y, dentro de la ventana de
normalización (ALERTA_VENTANA_NORMALIZACION_MINUTOS), sale otra vez, eso es una
excursión NUEVA: debe nacer una alerta PENDIENTE (visible entre las pendientes
del dashboard) y avisarse por correo. Antes, `generar_alerta` reabría la alerta
atendida: la excursión quedaba escondida dentro de un episodio «atendido», fuera
de las pendientes y sin correo (el despachador la daba por notificada)."""

from datetime import datetime, timedelta, timezone

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.domain.value_objects.rol import Rol
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from tests.conftest import auth_header

DEVICE_ID = "FARM-HU21-REAB"


class _Notificaciones:
    """Doble del despachador: registra lo que se encola tras el COMMIT."""

    def __init__(self) -> None:
        self.avisos: list = []

    def encolar(self, aviso) -> bool:
        self.avisos.append(aviso)
        return True

    def encolar_todos(self, avisos) -> None:
        for aviso in avisos:
            self.encolar(aviso)

    async def detener(self, timeout: float = 5.0) -> None:  # lo llama el lifespan al cerrar
        return None


def _lectura(client, token, instante: datetime, temperatura: float) -> None:
    respuesta = client.post(
        "/api/lecturas",
        json={
            "device_id": DEVICE_ID,
            "timestamp": instante.isoformat(),
            "temperatura_interna": temperatura,
            "temperatura_ambiental": 21.0,
            "humedad_ambiental": 55.0,
            "apertura_refrigerador": False,
            "estado_conectividad": "online",
        },
        headers=auth_header(token),
    )
    assert respuesta.status_code == 201, respuesta.text


def _alertas(client, token, **filtros) -> list[dict]:
    respuesta = client.get("/api/alertas", params={"device_id": DEVICE_ID, **filtros}, headers=auth_header(token))
    assert respuesta.status_code == 200
    return respuesta.json()


def test_excursion_tras_una_alerta_atendida_crea_una_alerta_pendiente_nueva_y_la_avisa(
    client, app, token_tecnico, token_farmaceutico
):
    notificaciones = _Notificaciones()
    app.state.notificaciones = notificaciones
    inicio = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=30)

    # 1. Excursión crítica → alerta PENDIENTE y su aviso.
    _lectura(client, token_tecnico, inicio, 9.6)
    [atendida] = _alertas(client, token_farmaceutico)
    assert atendida["estado"] == "pendiente"

    # 2. El QF la reconoce y registra la acción correctiva → ATENDIDA.
    assert client.patch(
        f"/api/alertas/{atendida['id']}/revisar", headers=auth_header(token_farmaceutico)
    ).status_code == 200
    accion = client.post(
        f"/api/alertas/{atendida['id']}/acciones-correctivas",
        json={
            "decision": "Mantener el lote en cuarentena",
            "descripcion": "Se verificó el cierre de la puerta y se trasladó el lote.",
            "justificacion": "Excursión breve por encima de 8 °C.",
        },
        headers=auth_header(token_farmaceutico),
    )
    assert accion.status_code == 201, accion.text

    # 3. Vuelve al rango: el episodio se cierra (HU-21 esc. 2).
    _lectura(client, token_tecnico, inicio + timedelta(minutes=2), 5.0)
    # 4. Nueva excursión 5 min después, dentro de la ventana de normalización.
    _lectura(client, token_tecnico, inicio + timedelta(minutes=7), 10.4)

    pendientes = _alertas(client, token_farmaceutico, estado="pendiente")
    assert len(pendientes) == 1
    nueva = pendientes[0]
    assert nueva["id"] != atendida["id"]
    assert nueva["episodio_abierto"] is True
    assert nueva["nivel_riesgo"] == NivelRiesgo.EXCURSION_CRITICA.value
    assert nueva["temperatura_pico"] == 10.4

    # La atendida conserva su ciclo cerrado: no se reabre ni cambia de estado.
    original = next(a for a in _alertas(client, token_farmaceutico) if a["id"] == atendida["id"])
    assert original["estado"] == "atendida"
    assert original["episodio_abierto"] is False
    assert original["cerrada_en"] is not None

    # Un aviso por la primera excursión y exactamente uno nuevo por la segunda.
    assert [str(a.alerta_id) for a in notificaciones.avisos] == [atendida["id"], nueva["id"]]
    assert notificaciones.avisos[-1].temperatura == 10.4


def test_una_alerta_reconocida_sin_accion_si_se_reabre_dentro_de_la_ventana(
    client, app, token_tecnico, token_farmaceutico
):
    """El cambio solo excluye las ATENDIDAS: una alerta que sigue a la espera de
    la acción correctiva conserva la histéresis anti-flapping (HU-20)."""
    app.state.notificaciones = _Notificaciones()
    inicio = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    _lectura(client, token_tecnico, inicio, 9.6)
    [alerta] = _alertas(client, token_farmaceutico)
    client.patch(f"/api/alertas/{alerta['id']}/revisar", headers=auth_header(token_farmaceutico))

    _lectura(client, token_tecnico, inicio + timedelta(minutes=2), 5.0)
    _lectura(client, token_tecnico, inicio + timedelta(minutes=7), 10.4)

    alertas = _alertas(client, token_farmaceutico)
    assert [a["id"] for a in alertas] == [alerta["id"]]
    assert alertas[0]["estado"] == "reconocida"
    assert alertas[0]["episodio_abierto"] is True


# ── Reproducción de scratchpad/hu21_reapertura_check.py ──────────────────


def _lectura_termica(instante: datetime, temperatura: float) -> LecturaTermica:
    return LecturaTermica(
        device_id=DEVICE_ID, timestamp=instante, temperatura_ambiental=21.0, humedad_ambiental=55.0,
        temperatura_interna=temperatura, apertura_refrigerador=False, estado_conectividad="online",
    )


def _caso_de_uso(session) -> RegistrarLecturaTermicaUseCase:
    return RegistrarLecturaTermicaUseCase(
        SQLAlchemyLecturaRepository(session), SQLAlchemyAlertaRepository(session),
        SQLAlchemyTrazabilidadRepository(session),
        ClasificarRiesgoTermicoUseCase(get_random_forest_service(), "experimental"),
        device_repository=SQLAlchemyDeviceRepository(session), registro_dispositivos_estricto=False,
        ia_legado_modo="experimental",
    )


async def test_reproduccion_la_alerta_atendida_no_se_reabre(db_session_factory, crear_usuario):
    """Misma secuencia que el script de la validación (reproducido en 7042304):
    excursión → ATENDIDA → recuperación → excursión a los pocos minutos."""
    qf = await crear_usuario("QF Reapertura", "qf.reapertura@farmacia.example.org", "password123", Rol.FARMACEUTICO)
    base = datetime.now(tz=timezone.utc).replace(microsecond=0) - timedelta(hours=1)

    async with db_session_factory() as session:
        for minuto, temperatura in ((0, 5.0), (1, 5.0), (2, 9.5), (3, 9.6)):
            await _caso_de_uso(session).execute(_lectura_termica(base + timedelta(minutes=minuto), temperatura))
        await session.commit()

    async with db_session_factory() as session:
        repositorio = SQLAlchemyAlertaRepository(session)
        episodio = await repositorio.obtener_episodio_abierto(DEVICE_ID)
        episodio.reconocer(qf.id, base + timedelta(minutes=3, seconds=30))
        episodio.marcar_atendida(base + timedelta(minutes=3, seconds=40))
        await repositorio.actualizar(episodio)
        await session.commit()
        atendida_id = episodio.id

    async with db_session_factory() as session:
        for minuto, temperatura in ((4, 5.0), (5, 5.0)):
            await _caso_de_uso(session).execute(_lectura_termica(base + timedelta(minutes=minuto), temperatura))
        await session.commit()

    async with db_session_factory() as session:
        caso = _caso_de_uso(session)
        await caso.execute(_lectura_termica(base + timedelta(minutes=10), 10.5))
        avisos = list(caso.avisos_pendientes)
        await _caso_de_uso(session).execute(_lectura_termica(base + timedelta(minutes=11), 10.8))
        await session.commit()

    async with db_session_factory() as session:
        repositorio = SQLAlchemyAlertaRepository(session)
        nueva = await repositorio.obtener_episodio_abierto(DEVICE_ID)
        atendida = await repositorio.obtener_por_id(atendida_id)

    assert nueva.id != atendida_id
    assert nueva.estado == EstadoAlerta.PENDIENTE
    assert nueva.temperatura_pico == 10.8
    assert atendida.estado == EstadoAlerta.ATENDIDA
    assert atendida.episodio_abierto is False
    assert [a.alerta_id for a in avisos] == [nueva.id]
