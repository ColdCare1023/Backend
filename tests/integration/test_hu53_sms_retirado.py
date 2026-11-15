"""HU-53 (v3.3): «Notificación SMS retirada del alcance; el aviso externo es
solo por correo (HU-52)».

El canal SMS estaba inactivo (SMS_ENABLED=false) pero seguía en el código: la
configuración, el envío por una pasarela tipo Twilio, el SMS de bienvenida y su
auditoría. Se retira sin romper despliegues que aún tengan variables SMS_* en
el entorno: se ignoran. Los campos `telefono` y `responsable_telefono` de la API
se conservan por compatibilidad, pero ya no envían nada."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from src.infrastructure.config import Settings
from src.infrastructure.database.models import AuditLogModel
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.notifications import bienvenida
from src.infrastructure.notifications.despachador import DespachadorNotificaciones
from src.infrastructure.notifications.notificacion_service import NotificacionService
from src.interface import main

DEVICE_ID = "FARM-HU53-SMS"
VARIABLES_SMS = {
    "SMS_ENABLED": "true",
    "SMS_API_URL": "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
    "SMS_ACCOUNT_SID": "AC" + "0" * 32,
    "SMS_AUTH_TOKEN": "",  # incompleta a propósito: antes impedía arrancar en producción
    "SMS_FROM": "+15550001111",
}
_PRODUCCION = {
    "environment": "production",
    "jwt_secret_key": "x" * 40,
    "trazabilidad_hmac_key": "h" * 40,
    "allowed_hosts": ["coldcare.example.org"],
    "cors_origins": ["https://coldcare.example.org"],
    "database_url": "postgresql+asyncpg://u:p@db:5432/prod",
    "mqtt_enabled": False,
}


@pytest.fixture
def variables_sms_antiguas(monkeypatch):
    for clave, valor in VARIABLES_SMS.items():
        monkeypatch.setenv(clave, valor)


@pytest.fixture
def sin_red(monkeypatch):
    """Cualquier POST saliente (Twilio, Telegram…) hace fallar la prueba."""
    llamadas: list[str] = []

    async def post(self, url, *args, **kwargs):
        llamadas.append(str(url))
        raise AssertionError(f"llamada de red inesperada a {url}")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return llamadas


def test_las_variables_sms_antiguas_no_impiden_arrancar(variables_sms_antiguas):
    ajustes = Settings(_env_file=None, **_PRODUCCION)
    assert not any(nombre.startswith("sms_") for nombre in type(ajustes).model_fields)
    # También como argumentos (p. ej. un .env antiguo leído por un script).
    Settings(_env_file=None, **_PRODUCCION, sms_enabled=True, sms_account_sid="AC1")


def test_el_servicio_de_canales_opcionales_ya_no_tiene_sms():
    for retirado in ("enviar_sms_a", "_enviar_sms", "notificar_sin_datos"):
        assert not hasattr(NotificacionService, retirado), retirado
    assert not hasattr(bienvenida, "componer_sms")
    assert list(bienvenida.ResultadoBienvenida.__dataclass_fields__) == ["correo"]


async def test_una_excursion_no_intenta_ningun_sms(variables_sms_antiguas, sin_red):
    servicio = NotificacionService(Settings(_env_file=None, environment="test", sms_enabled=True))
    assert servicio.habilitado is False
    assert await servicio.notificar_excursion_critica("ESP32-01", 12.0, "2026-11-14T10:00:00+00:00") is None
    assert sin_red == []


class _Transporte:
    nombre = "espia"

    def __init__(self) -> None:
        self.enviados = []

    async def enviar(self, mensaje) -> None:
        self.enviados.append(mensaje)


class _Mensaje:
    def __init__(self, payload: dict) -> None:
        self.topic = f"farmacias/{DEVICE_ID}/lecturas"
        self.payload = json.dumps(payload).encode("utf-8")


class _BroadcasterMudo:
    async def publicar(self, evento, tipo="lectura"):
        return None


async def test_el_despachador_avisa_solo_por_correo_aunque_haya_telefono(
    db_session_factory, monkeypatch, variables_sms_antiguas, sin_red
):
    monkeypatch.setattr(main, "_session_factory", db_session_factory)
    async with db_session_factory() as session:
        repositorio = SQLAlchemyDeviceRepository(session)
        await repositorio.obtener_o_crear(DEVICE_ID)
        await repositorio.actualizar_responsable(DEVICE_ID, "QF", "qf@coldcare.pe", "+51999888777")
        await session.commit()
    transporte = _Transporte()
    despachador = DespachadorNotificaciones(
        Settings(
            _env_file=None, environment="test", email_enabled=True, email_from="avisos@coldcare.pe",
            frontend_url="https://coldcare.vercel.app", sms_enabled=True,
        ),
        db_session_factory, transporte, esperas_reintento=(3600,),
    )
    lectura = {
        "device_id": DEVICE_ID, "timestamp": (datetime.now(tz=timezone.utc) - timedelta(minutes=1)).isoformat(),
        "schema_version": 1, "reading_id": "hu53-sms-1", "temperatura_interna": 12.0,
        "temperatura_ambiental": 22.0, "humedad_ambiental": 55.0, "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }

    await main._procesar_lectura_mqtt(_Mensaje(lectura), _BroadcasterMudo(), None, despachador)
    aviso = despachador._cola.get_nowait()
    assert (await despachador.procesar(aviso)).estado == "enviado"

    assert [m.destinatario for m in transporte.enviados] == ["qf@coldcare.pe"]
    assert sin_red == []
    async with db_session_factory() as session:
        detalles = [f.detalle for f in (await session.execute(select(AuditLogModel))).scalars().all()]
    canales = {d.get("canal") for d in detalles if d.get("canal")}
    assert canales == {"email"}
