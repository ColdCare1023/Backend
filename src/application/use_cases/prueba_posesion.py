"""HU-44: prueba de posesión de la credencial tras una rotación por compromiso.

En EMQX Serverless (modo manual) el administrador cambia la contraseña en la
consola y DESPUÉS confirma en el sistema. Si confirma sin haberla cambiado, el
broker sigue aceptando el token filtrado; salir de la retención en ese momento
reabriría el hueco que la rotación quería cerrar. Por eso la retención termina
cuando el nodo responde un desafío firmado con el token de SU SESIÓN:

    clave     = SHA-256("coldcare-pop-v1:" + token)        (`pop_clave`, hex)
    respuesta = HMAC-SHA256(clave, nonce + ":" + device_id)

Si el broker rechazó el relevo y el nodo entró con el token anterior, firma con
ese token y la respuesta no coincide; quien solo tiene el token filtrado no
conoce la clave nueva. Un evento `CREDENCIAL_ROTADA` no bastaría: lo puede
publicar cualquiera que tenga el token viejo. El backend guarda la clave
derivada, nunca el token, y cada nonce sirve una sola vez.
"""

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.domain.repositories.i_device_repository import IDeviceRepository

PREFIJO_CLAVE = "coldcare-pop-v1:"
TIPO_DESAFIO = "desafio_posesion"
# El nodo puede tardar en reconectar tras la expulsión: el nonce vale 10 min.
VIGENCIA_DESAFIO = timedelta(minutes=10)
# Reenviar el desafío con cada mensaje retenido inundaría el tópico de acuses.
REEMISION_MINIMA = timedelta(seconds=30)
# Un nonce a punto de caducar no se reenvía: el nodo podría responderlo
# (reconexión, TLS) cuando ya no vale. Se reutiliza si le queda más de esto.
MARGEN_REUTILIZACION = timedelta(minutes=1)


def clave_posesion(token: str) -> str:
    return hashlib.sha256((PREFIJO_CLAVE + token).encode()).hexdigest()


def firma_esperada(clave_hex: str, nonce: str, device_id: str) -> str:
    return hmac.new(bytes.fromhex(clave_hex), f"{nonce}:{device_id}".encode(), hashlib.sha256).hexdigest()


def mensaje_desafio(device_id: str, nonce: str) -> str:
    """Se publica en `farmacias/{device_id}/ack` (el nodo ya lo escucha y la ACL
    ya permite al backend publicar ahí)."""
    return json.dumps({"device_id": device_id, "tipo": TIPO_DESAFIO, "nonce": nonce}, separators=(",", ":"))


def _como_utc(instante: datetime | None) -> datetime | None:
    if instante is None:
        return None
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


def espera_prueba(credencial: dict | None) -> bool:
    """Retención por compromiso con la consola ya confirmada."""
    return bool(credencial and credencial.get("rotacion_por_compromiso") and not credencial.get("broker_pendiente"))


def nonce_vigente(credencial: dict | None, ahora: datetime) -> str | None:
    """Nonce reutilizable: vigente y con más de MARGEN_REUTILIZACION por delante."""
    emitido = _como_utc((credencial or {}).get("pop_nonce_emitido_en"))
    nonce = (credencial or {}).get("pop_nonce")
    if not nonce or emitido is None or ahora - emitido > VIGENCIA_DESAFIO - MARGEN_REUTILIZACION:
        return None
    return nonce


async def emitir_desafio(dispositivos: IDeviceRepository, device_id: str, ahora: datetime) -> str:
    nonce = secrets.token_hex(16)
    await dispositivos.registrar_desafio_posesion(device_id, nonce, ahora)
    return nonce


async def preparar_desafio(
    dispositivos: IDeviceRepository, device_id: str, credencial: dict | None, ahora: datetime
) -> str | None:
    """Nonce que hay que (re)enviar al nodo, o None si no se espera prueba.

    Mientras el nonce vigente no caduque se reenvía EL MISMO: si se generara uno
    nuevo en cada reconexión, una respuesta al anterior que llegue tarde (el
    desafío de la API y el de la ingesta se cruzan en la reconexión) se
    auditaría como fallo aunque el nodo tenga el token correcto."""
    if not espera_prueba(credencial) or not credencial.get("pop_clave"):
        return None
    return nonce_vigente(credencial, ahora) or await emitir_desafio(dispositivos, device_id, ahora)


@dataclass(frozen=True, slots=True)
class ResultadoPosesion:
    verificada: bool
    motivo: str  # verificada | sin_retencion | consola_pendiente | sin_clave | nonce_desconocido | nonce_caducado | firma_invalida


async def verificar_posesion(
    dispositivos: IDeviceRepository, device_id: str, nonce: str, firma: str, ahora: datetime
) -> ResultadoPosesion:
    credencial = await dispositivos.obtener_credencial(device_id)
    if not credencial or not credencial.get("rotacion_por_compromiso"):
        return ResultadoPosesion(False, "sin_retencion")
    if credencial.get("broker_pendiente"):
        return ResultadoPosesion(False, "consola_pendiente")
    clave = credencial.get("pop_clave")
    if not clave:
        return ResultadoPosesion(False, "sin_clave")
    vigente = credencial.get("pop_nonce") or ""
    if not vigente or not hmac.compare_digest(vigente, nonce):
        return ResultadoPosesion(False, "nonce_desconocido")
    emitido = _como_utc(credencial.get("pop_nonce_emitido_en"))
    if emitido is None or ahora - emitido > VIGENCIA_DESAFIO:
        return ResultadoPosesion(False, "nonce_caducado")
    if not hmac.compare_digest(firma_esperada(clave, nonce, device_id), firma.lower()):
        return ResultadoPosesion(False, "firma_invalida")
    await dispositivos.concluir_rotacion_por_compromiso(device_id)
    return ResultadoPosesion(True, "verificada")
