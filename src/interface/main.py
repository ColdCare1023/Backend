import contextlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import aiomqtt
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import ValidationError
from sqlalchemy import text

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.exceptions import DispositivoNoAutorizadoError, LecturaInvalidaError
from src.domain.value_objects.rol import Rol
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.audit_log_repository import (
    SQLAlchemyAuditLogRepository,
)
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.database.session import _session_factory
from src.infrastructure.mqtt import contrato_v1
from src.infrastructure.mqtt.mqtt_client import ESTADO_CONEXION, mqtt_session
from src.infrastructure.mqtt.payload_schema import (
    MAX_BYTES_PAYLOAD_MQTT,
    EventoDispositivoPayload,
    LecturaPayload,
    TipoEventoDispositivo,
)
from src.infrastructure.notifications.notificacion_service import NotificacionService
from src.infrastructure.security.rate_limiter import SlidingWindowRateLimiter
from src.infrastructure.security.revocation_store import JtiStore
from src.interface.api.alertas_router import router as alertas_router
from src.interface.api.api_protection import instalar_proteccion_api
from src.interface.api.auditoria_router import router as auditoria_router
from src.interface.api.auth_router import router as auth_router
from src.interface.api.checklist_router import router as checklist_router
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.dispositivos_router import router as dispositivos_router
from src.interface.api.firmware_router import router as firmware_router
from src.interface.api.ia_router import router as ia_router
from src.interface.api.lecturas_router import router as lecturas_router
from src.interface.api.mappers import evidencia_edge, lectura_to_response
from src.interface.api.reportes_router import router as reportes_router
from src.interface.api.security_headers import instalar_security_headers
from src.interface.api.sse_broadcaster import SSEBroadcaster
from src.interface.api.sse_router import router as sse_router
from src.interface.api.trazabilidad_router import router as trazabilidad_router
from src.interface.api.usuarios_router import router as usuarios_router

logger = logging.getLogger("interface.main")


def _device_id_del_topic(topic: str, sufijo: str = "lecturas") -> str | None:
    """En `farmacias/{device_id}/{sufijo}` el segmento intermedio identifica
    al dispositivo autenticado ante el broker."""
    partes = topic.split("/")
    if len(partes) != 3 or partes[0] != "farmacias" or partes[2] != sufijo:
        return None
    return partes[1] or None


def _topico_ack(device_id: str) -> str:
    """HU-07: tópico donde el backend confirma, a nivel de aplicación, que una
    lectura hizo COMMIT en PostgreSQL. El ESP32 debe esperar este mensaje
    (no solo el PUBACK de transporte) antes de borrar el bloque de LittleFS."""
    return f"farmacias/{device_id}/ack"


async def _publicar_ack_lectura(
    client: aiomqtt.Client | None,
    device_id: str,
    reading_id: str | None,
    timestamp: datetime,
    estado: str = "commit_confirmado",
    boot_id: int | None = None,
    seq_no: int | None = None,
) -> None:
    """HU-07 Escenario 1: el acuse lógico se publica DESPUÉS de que la
    transacción de la lectura ya hizo COMMIT (se llama tras `session.commit()`
    en `_procesar_lectura_mqtt`), nunca antes. `client=None` en pruebas que
    invocan el manejador directamente sin un broker real: el acuse se omite
    sin romper el resto del pipeline, igual que hace `broadcaster.publicar`
    cuando no hay suscriptores SSE.

    `estado` distingue un COMMIT real (`"commit_confirmado"`) de un rechazo
    PERMANENTE ya auditado (`estado != "ok"`/dispositivo no autorizado,
    timestamp inválido): el firmware solo necesita SABER que el backend
    terminó de procesar esta lectura —para dejar de reintentarla y liberar
    el bloque de LittleFS— no reinterpretar el motivo. Sin esto, una lectura
    permanentemente rechazada nunca recibía acuse y el nodo la reintentaba
    para siempre, bloqueando toda la cola FIFO detrás de ella (core::drenar
    se detiene en el primer fallo de publicación)."""
    if client is None:
        return
    acuse: dict = {
        "device_id": device_id,
        "reading_id": reading_id,
        "timestamp": timestamp.isoformat(),
        "estado": estado,
    }
    # Contrato v1.0 (firmware ColdCare): el nodo correlaciona por boot_id+seq_no
    # y decide por `status` si libera el registro o lo pasa a cuarentena.
    if boot_id is not None and seq_no is not None:
        acuse.update(boot_id=boot_id, seq_no=seq_no, status=contrato_v1.STATUS_ACK.get(estado, "rejected"))
    cuerpo = json.dumps(
        acuse,
        # Compacto a propósito: el firmware extrae `reading_id` con una búsqueda de
        # texto plano (`"reading_id":"..."`, core::extraerCampoString) y no tolera el
        # espacio tras los dos puntos que emite json.dumps por defecto.
        separators=(",", ":"),
    )
    try:
        await client.publish(_topico_ack(device_id), payload=cuerpo, qos=1)
    except Exception:  # pragma: no cover - defensa: un fallo de publish no debe tumbar la ingesta
        logger.exception("No se pudo publicar el acuse lógico MQTT para %s", device_id)


def _payload_en_conflicto(existente, nueva) -> bool:
    """True si dos lecturas con la misma identidad traen valores distintos."""
    def distinto(a, b) -> bool:
        if a is None or b is None:
            return a is not b
        return abs(float(a) - float(b)) > 0.005

    return (
        distinto(existente.temperatura_interna, nueva.temperatura_interna)
        or distinto(existente.temperatura_ambiental, nueva.temperatura_ambiental)
        or distinto(existente.humedad_ambiental, nueva.humedad_ambiental)
        or existente.apertura_refrigerador != nueva.apertura_refrigerador
    )


async def _procesar_mensaje_mqtt(
    message: aiomqtt.Message, broadcaster: SSEBroadcaster, client: aiomqtt.Client | None = None
) -> None:
    """Despacha según el tópico. B-09: los mensajes de `/eventos` tienen su
    propio esquema; antes se validaban contra LecturaPayload, fallaban y se
    descartaban en silencio, así que las desconexiones del nodo nunca se
    registraban."""
    topico = str(message.topic)
    if topico.endswith("/eventos"):
        return await _procesar_evento_mqtt(message, broadcaster)
    return await _procesar_lectura_mqtt(message, broadcaster, client)


# Contrato v1.0: `event_type` → tipo interno. Los que no tienen equivalente se
# auditan con su propio nombre (ver `_evento_desde_v1`).
_EVENTOS_V1 = {
    "online": TipoEventoDispositivo.LWT_ONLINE,
    "lwt_offline": TipoEventoDispositivo.LWT_OFFLINE,
    "buffer_80": TipoEventoDispositivo.BUFFER_CAPACIDAD_ALTA,
    "buffer_saturado": TipoEventoDispositivo.BUFFER_SATURADO,
    "wifi_reconexion_prolongada": TipoEventoDispositivo.WIFI_RECONEXION_PROLONGADA,
    "conmutacion_respaldo": TipoEventoDispositivo.CONMUTACION_RESPALDO,
    "suministro_restablecido": TipoEventoDispositivo.CONMUTACION_RESPALDO,
    "respaldo_autonomia_critica": TipoEventoDispositivo.RESPALDO_BAJO,
}


async def _procesar_evento_v1(
    message: aiomqtt.Message, crudo: dict, broadcaster: SSEBroadcaster
) -> None:
    """Eventos del firmware ColdCare (contrato v1.0)."""
    try:
        evento = contrato_v1.EventoV1.model_validate(crudo)
    except ValidationError as exc:
        logger.warning("Evento v1.0 inválido en tópico %s: %s", message.topic, exc.error_count())
        return
    device_topic = _device_id_del_topic(str(message.topic), sufijo="eventos")
    if device_topic is None or device_topic != evento.device_id:
        logger.warning("Descartado: evento v1.0 de %s en tópico de %s", evento.device_id, device_topic)
        return

    ocurrido = evento.occurred_at or datetime.now(timezone.utc)
    sse = {
        "device_id": evento.device_id,
        "tipo_evento": evento.event_type,
        "timestamp": ocurrido.isoformat(),
        "detalle": evento.detail,
        "boot_id": evento.boot_id,
    }
    # estado_nodo llega cada 5 min: es telemetría de salud, no un hecho auditable.
    # Se emite por SSE para el dashboard pero no se escribe en la cadena de auditoría.
    if evento.event_type == "estado_nodo":
        await broadcaster.publicar(sse, "estado_nodo")
        return

    async with _session_factory() as session:
        device_repository = SQLAlchemyDeviceRepository(session)
        auditoria = AuditarAccionCriticaUseCase(
            SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
        )
        if not await device_repository.existe(evento.device_id):
            await auditoria.execute(
                usuario_id=None,
                accion="EVENTO_DISPOSITIVO_DESCONOCIDO",
                recurso="mqtt/eventos",
                detalle={"device_id": evento.device_id, "tipo_evento": evento.event_type},
                ip_origen=None,
            )
            await session.commit()
            return

        tipo = _EVENTOS_V1.get(evento.event_type)
        if tipo is TipoEventoDispositivo.LWT_OFFLINE:
            await device_repository.actualizar_estado_conectividad(evento.device_id, "offline")
        elif tipo is TipoEventoDispositivo.LWT_ONLINE:
            await device_repository.actualizar_estado_conectividad(evento.device_id, "online")
        accion = {
            TipoEventoDispositivo.LWT_OFFLINE: "DISPOSITIVO_OFFLINE",
            TipoEventoDispositivo.LWT_ONLINE: "DISPOSITIVO_ONLINE",
        }.get(tipo, (tipo.value if tipo else evento.event_type).upper())
        detalle: dict = {"tipo_evento": evento.event_type, "boot_id": evento.boot_id}
        if evento.detail:
            detalle["detalle"] = evento.detail
        if evento.occurred_at:
            detalle["occurred_at"] = evento.occurred_at.isoformat()
        await auditoria.execute(
            usuario_id=None,
            accion=accion,
            recurso=f"dispositivos/{evento.device_id}",
            detalle=detalle,
            ip_origen=None,
            device_id=evento.device_id,
        )
        await session.commit()

    await broadcaster.publicar(sse, evento.event_type)


async def _procesar_evento_mqtt(message: aiomqtt.Message, broadcaster: SSEBroadcaster) -> None:
    try:
        crudo = json.loads(message.payload)
    except ValueError:
        crudo = None
    if contrato_v1.es_v1(crudo):
        await _procesar_evento_v1(message, crudo, broadcaster)
        return
    try:
        evento = EventoDispositivoPayload.model_validate_json(message.payload)
    except ValidationError as exc:
        logger.warning("Evento MQTT inválido en tópico %s: %s", message.topic, exc.error_count())
        return

    # Mismo control anti-suplantación que en las lecturas: el device_id del
    # cuerpo debe coincidir con el tópico sobre el que el broker autorizó
    # publicar; si no, un nodo podría declarar offline a otro.
    device_topic = _device_id_del_topic(str(message.topic), sufijo="eventos")
    if device_topic is None or device_topic != evento.device_id:
        logger.warning(
            "Descartado: evento con device_id (%s) que no coincide con el tópico (%s)",
            evento.device_id,
            device_topic,
        )
        return

    async with _session_factory() as session:
        device_repository = SQLAlchemyDeviceRepository(session)
        auditoria = AuditarAccionCriticaUseCase(
                SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
            )
        # Un evento sobre un dispositivo no provisionado no debe crearlo por la
        # puerta de atrás: se audita y se descarta.
        if not await device_repository.existe(evento.device_id):
            await auditoria.execute(
                usuario_id=None,
                accion="EVENTO_DISPOSITIVO_DESCONOCIDO",
                recurso="mqtt/eventos",
                detalle={"device_id": evento.device_id, "tipo_evento": evento.tipo_evento.value},
                ip_origen=None,
            )
            await session.commit()
            logger.warning("Evento de dispositivo no registrado: %s", evento.device_id)
            return

        detalle_auditoria: dict = {"tipo_evento": evento.tipo_evento.value}
        if evento.detalle:
            detalle_auditoria["detalle"] = evento.detalle

        match evento.tipo_evento:
            case TipoEventoDispositivo.LWT_OFFLINE:
                await device_repository.actualizar_estado_conectividad(evento.device_id, "offline")
                accion, tipo_sse = "DISPOSITIVO_OFFLINE", "desconexion"
            case TipoEventoDispositivo.LWT_ONLINE:
                await device_repository.actualizar_estado_conectividad(evento.device_id, "online")
                accion, tipo_sse = "DISPOSITIVO_ONLINE", "reconexion"
            case TipoEventoDispositivo.ERROR_SENSOR:
                accion, tipo_sse = "ERROR_SENSOR", "fallo_sensor"
            case TipoEventoDispositivo.BUFFER_SATURADO:
                # HU-06 criterio 3: el `detalle` trae el conteo y el periodo
                # afectado (lo arma el firmware); aquí solo se audita, no se
                # reinterpreta — es evidencia de auditoría, no un dato
                # estructurado sobre el que este backend deba operar.
                accion, tipo_sse = "BUFFER_SATURADO", "saturacion_buffer"
            case TipoEventoDispositivo.BUFFER_CAPACIDAD_ALTA:
                accion, tipo_sse = "BUFFER_CAPACIDAD_ALTA", "capacidad_buffer_alta"
            case TipoEventoDispositivo.WIFI_RECONEXION_PROLONGADA:
                accion, tipo_sse = "WIFI_RECONEXION_PROLONGADA", "reconexion_prolongada"
            case TipoEventoDispositivo.CONMUTACION_RESPALDO:
                # HU-52 escenario 1: solo se audita/traza — el nodo sigue
                # midiendo y persistiendo en LittleFS (HU-06) igual que
                # cualquier otro periodo, con o sin red.
                accion, tipo_sse = "CONMUTACION_RESPALDO", "conmutacion_respaldo"
            case TipoEventoDispositivo.RESPALDO_BAJO:
                # HU-52 escenario 3: diagnóstico antes de un eventual apagado
                # ordenado del nodo — no implica que ya se haya apagado.
                accion, tipo_sse = "RESPALDO_BAJO", "respaldo_bajo"
            case TipoEventoDispositivo.FIRMWARE_UPDATE:
                if evento.firmware_version:
                    await device_repository.actualizar_firmware_version(
                        evento.device_id, evento.firmware_version
                    )
                    detalle_auditoria["firmware_version"] = evento.firmware_version
                accion, tipo_sse = "FIRMWARE_ACTUALIZADO", "firmware_actualizado"

        await auditoria.execute(
            usuario_id=None,
            accion=accion,
            recurso=f"dispositivos/{evento.device_id}",
            detalle=detalle_auditoria,
            ip_origen=None,
            device_id=evento.device_id,
        )
        await session.commit()

    await broadcaster.publicar(
        {
            "device_id": evento.device_id,
            "tipo_evento": evento.tipo_evento.value,
            "timestamp": evento.timestamp.isoformat(),
            "detalle": evento.detalle,
            "firmware_version": evento.firmware_version,
        },
        tipo_sse,
    )


async def _rechazar_payload_invalido(
    message: aiomqtt.Message,
    exc: ValidationError | None,
    client: aiomqtt.Client | None,
    errores: list[dict] | None = None,
) -> None:
    """HU-15: un payload que no cumple el contrato se rechaza sin detener la
    ingesta, queda auditado y —si trae `reading_id`— recibe acuse. Sin acuse el
    nodo reintentaría ese archivo para siempre y su cola FIFO no avanzaría."""
    device_id = _device_id_del_topic(str(message.topic))
    if exc is not None:
        errores = [
            {"campo": ".".join(str(p) for p in e["loc"]), "tipo": e["type"]} for e in exc.errors()[:10]
        ]
    logger.warning("Payload MQTT rechazado en %s: %s", message.topic, errores)
    if device_id is None:
        return
    try:
        crudo = json.loads(message.payload)
    except ValueError:
        crudo = None
    reading_id = crudo.get("reading_id") if isinstance(crudo, dict) else None
    boot_id = seq_no = None
    if isinstance(crudo, dict):
        try:
            boot_id, seq_no = int(crudo["boot_id"]), int(crudo["seq_no"])
        except (KeyError, TypeError, ValueError):
            boot_id = seq_no = None

    async with _session_factory() as session:
        await AuditarAccionCriticaUseCase(
            SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
        ).execute(
            usuario_id=None,
            accion="LECTURA_RECHAZADA_ESQUEMA",
            recurso="mqtt/lecturas",
            detalle={
                "device_id": device_id, "reading_id": reading_id,
                "boot_id": boot_id, "seq_no": seq_no, "errores": errores,
            },
            ip_origen=None,
        )
        await session.commit()

    if (isinstance(reading_id, str) and reading_id) or (boot_id is not None and seq_no is not None):
        await _publicar_ack_lectura(
            client, device_id, reading_id, datetime.now(timezone.utc), estado="payload_rechazado",
            boot_id=boot_id, seq_no=seq_no,
        )


async def _procesar_lectura_mqtt(
    message: aiomqtt.Message, broadcaster: SSEBroadcaster, client: aiomqtt.Client | None = None
) -> None:
    # El tamaño se comprueba ANTES de deserializar: la ingesta MQTT no pasa por
    # el middleware que acota el cuerpo de las peticiones REST, así que este es
    # el único punto donde se puede rechazar un mensaje desmesurado sin haberlo
    # convertido antes en objetos de Python.
    if len(message.payload) > MAX_BYTES_PAYLOAD_MQTT:
        logger.warning(
            "Descartado: payload MQTT de %d bytes en %s (máximo %d).",
            len(message.payload),
            message.topic,
            MAX_BYTES_PAYLOAD_MQTT,
        )
        return

    try:
        crudo = json.loads(message.payload)
    except ValueError:
        crudo = None
    telemetria_v1: contrato_v1.TelemetriaV1 | None = None
    try:
        if contrato_v1.es_v1(crudo):
            # Contrato v1.0 (firmware ColdCare1023/IoT): se valida con su propio
            # esquema y se traduce al modelo interno; el resto del pipeline no cambia.
            if len(message.payload) > contrato_v1.PAYLOAD_MAX_BYTES:
                await _rechazar_payload_invalido(
                    message, None, client,
                    errores=[{"campo": "payload", "tipo": f"excede_{contrato_v1.PAYLOAD_MAX_BYTES}_bytes"}],
                )
                return
            telemetria_v1 = contrato_v1.TelemetriaV1.model_validate(crudo)
            payload = LecturaPayload.model_validate(
                contrato_v1.a_lectura_interna(telemetria_v1, datetime.now(timezone.utc))
            )
        else:
            payload = LecturaPayload.model_validate_json(message.payload)
    except ValidationError as exc:
        await _rechazar_payload_invalido(message, exc, client)
        return
    settings = get_settings()

    # Anti-suplantación: el device_id declarado en el payload debe coincidir
    # con el segmento del tópico sobre el que el broker autorizó publicar.
    device_topic = _device_id_del_topic(str(message.topic))
    if device_topic is None or device_topic != payload.device_id:
        logger.warning(
            "Descartado: tópico MQTT inválido o device_id (%s) no coincide con el tópico (%s)",
            payload.device_id,
            device_topic,
        )
        return

    async with _session_factory() as session:
        lectura_repository = SQLAlchemyLecturaRepository(session)
        alerta_repository = SQLAlchemyAlertaRepository(session)
        trazabilidad_repository = SQLAlchemyTrazabilidadRepository(session)
        clasificar_use_case = ClasificarRiesgoTermicoUseCase(get_random_forest_service())
        use_case = RegistrarLecturaTermicaUseCase(
            lectura_repository,
            alerta_repository,
            trazabilidad_repository,
            clasificar_use_case,
            device_repository=SQLAlchemyDeviceRepository(session),
            registro_dispositivos_estricto=settings.device_registry_estricto,
            audit_log_repository=SQLAlchemyAuditLogRepository(session),
            notificacion_service=NotificacionService(settings),
            ventana_normalizacion_alerta_minutos=settings.alerta_ventana_normalizacion_minutos,
        )

        lectura = LecturaTermica(
            device_id=payload.device_id,
            timestamp=payload.timestamp,
            temperatura_ambiental=payload.temperatura_ambiental,
            humedad_ambiental=payload.humedad_ambiental,
            temperatura_interna=payload.temperatura_interna,
            apertura_refrigerador=payload.apertura_refrigerador,
            estado_conectividad=payload.estado_conectividad,
            reading_id=payload.reading_id,
            schema_version=payload.schema_version,
            boot_id=payload.boot_id,
            seq_no=payload.seq_no,
            time_quality=payload.time_quality,
            uptime_ms=payload.uptime_ms,
            payload=evidencia_edge(
                firmware_version=payload.firmware_version,
                duracion_apertura_segundos=payload.duracion_apertura_segundos,
            ),
        )
        if telemetria_v1 is not None:
            lectura.payload = {**(lectura.payload or {}), **contrato_v1.evidencia_v1(telemetria_v1)}
        # Reenvío QoS1: no publicar un segundo SSE lógico. El caso de uso
        # conserva misma garantía en BD; este guard evita notificación doble.
        # HU-11: con boot_id+seq_no (firmware nuevo) esa es la identidad real
        # de la lectura; sin ellos se conserva device_id+timestamp.
        # Con boot_id+seq_no, el timestamp NO identifica la lectura: sin NTP dos
        # arranques repiten marcas (migración 0015).
        if payload.boot_id is not None and payload.seq_no is not None:
            existente = await lectura_repository.obtener_por_device_boot_seq(
                payload.device_id, payload.boot_id, payload.seq_no
            )
        else:
            existente = await lectura_repository.obtener_por_device_y_timestamp(
                payload.device_id, payload.timestamp
            )
        if existente is not None:
            logger.info("Lectura MQTT duplicada omitida: %s/%s", payload.device_id, payload.timestamp)
            # HU-11: un reenvío idéntico es un duplicado inocuo; el mismo identificador
            # con valores distintos es un CONFLICTO y no se puede descartar sin dejar
            # rastro: la lectura original se conserva y la recibida queda auditada.
            if _payload_en_conflicto(existente, lectura):
                auditoria = AuditarAccionCriticaUseCase(
                    SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
                )
                await auditoria.execute(
                    usuario_id=None,
                    accion="LECTURA_CONFLICTO",
                    recurso="mqtt/lecturas",
                    detalle={
                        "device_id": payload.device_id,
                        "timestamp": payload.timestamp.isoformat(),
                        "reading_id_recibido": payload.reading_id,
                        "boot_id_recibido": payload.boot_id,
                        "seq_no_recibido": payload.seq_no,
                        "reading_id_existente": existente.reading_id,
                        "temperatura_interna_recibida": payload.temperatura_interna,
                        "temperatura_ambiental_recibida": payload.temperatura_ambiental,
                        "humedad_ambiental_recibida": payload.humedad_ambiental,
                    },
                    ip_origen=None,
                )
                await session.commit()
                logger.warning(
                    "Conflicto de lectura MQTT %s/%s: mismo identificador, contenido distinto.",
                    payload.device_id, payload.timestamp,
                )
                await _publicar_ack_lectura(
                    client, payload.device_id, payload.reading_id, payload.timestamp,
                    estado="conflicto_payload", boot_id=payload.boot_id, seq_no=payload.seq_no,
                )
                return
            # HU-07 Escenario 2: el nodo puede reenviar un bloque cuyo COMMIT
            # ya ocurrió (el acuse anterior se perdió, o reanudó desde un
            # archivo previo al último confirmado). Confirmar de nuevo permite
            # que LittleFS lo borre en vez de reintentar indefinidamente.
            await _publicar_ack_lectura(
                client, payload.device_id, payload.reading_id or existente.reading_id, payload.timestamp,
                estado="duplicado", boot_id=payload.boot_id, seq_no=payload.seq_no,
            )
            return

        episodio_previo = await alerta_repository.obtener_episodio_abierto(payload.device_id)
        try:
            lectura_guardada = await use_case.execute(lectura)
        except DispositivoNoAutorizadoError:
            auditoria = AuditarAccionCriticaUseCase(
                SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
            )
            await auditoria.execute(
                usuario_id=None,
                accion="DISPOSITIVO_RECHAZADO",
                recurso="mqtt/lecturas",
                detalle={"device_id": payload.device_id},
                ip_origen=None,
            )
            await session.commit()
            logger.warning("Dispositivo no registrado rechazado: %s", payload.device_id)
            # HU-07: rechazo PERMANENTE ya auditado — sin acuse, el firmware
            # reintentaría este bloque para siempre y bloquearía toda la cola
            # FIFO detrás de él.
            await _publicar_ack_lectura(
                client, payload.device_id, payload.reading_id, payload.timestamp,
                estado="dispositivo_no_autorizado", boot_id=payload.boot_id, seq_no=payload.seq_no,
            )
            return
        except LecturaInvalidaError as exc:
            # El caso de uso ya dejó el motivo del rechazo en audit_logs (p. ej.
            # LECTURA_RECHAZADA_TIMESTAMP); hay que confirmarlo o se pierde al
            # cerrarse la sesión sin commit.
            await session.commit()
            logger.warning(
                "Lectura inválida descartada para device %s: %s", payload.device_id, exc
            )
            # HU-07: mismo motivo que arriba — rechazo permanente, no un fallo
            # transitorio, así que también necesita acuse para liberar LittleFS.
            await _publicar_ack_lectura(
                client, payload.device_id, payload.reading_id, payload.timestamp,
                estado="lectura_invalida", boot_id=payload.boot_id, seq_no=payload.seq_no,
            )
            return
        episodio_actual = await alerta_repository.obtener_episodio_abierto(payload.device_id)
        # HU-06: si este arranque ya tiene una lectura sincronizada, reconstruye la
        # hora de las capturadas sin NTP (incluida esta, si llegó sin sincronizar).
        if lectura_guardada.boot_id is not None:
            await lectura_repository.reanclar_no_sincronizadas(
                lectura_guardada.device_id, lectura_guardada.boot_id
            )
        await session.commit()

    # HU-07 Escenario 1: el acuse lógico se publica DESPUÉS del COMMIT, nunca
    # antes ni en su lugar. Es lo único que autoriza al firmware a borrar el
    # bloque correspondiente de LittleFS.
    await _publicar_ack_lectura(
        client, lectura_guardada.device_id, lectura_guardada.reading_id, lectura_guardada.timestamp,
        boot_id=lectura_guardada.boot_id, seq_no=lectura_guardada.seq_no,
    )

    evento_lectura = lectura_to_response(lectura_guardada).model_dump(mode="json")
    if lectura_guardada.estado_inferencia == "omitida":
        tipo = "fallo_sensor" if lectura_guardada.origen_clasificacion == "fallo_sensor" else "inferencia_omitida"
    else:
        tipo = "lectura"
    await broadcaster.publicar(evento_lectura, tipo)

    # HU-18/21/34: se sigue riesgo_efectivo, no nivel_riesgo/model_class —
    # una excursión confirmada por rango abre/mantiene episodio de alerta
    # aunque la IA no haya podido clasificar la lectura.
    if lectura_guardada.riesgo_efectivo is not None and episodio_previo is not None and episodio_actual is None:
        await broadcaster.publicar(evento_lectura, "recuperacion")
    elif lectura_guardada.riesgo_efectivo is not None and episodio_actual is not None:
        tipo_episodio = "alerta" if episodio_previo is None else "episodio_actualizado"
        await broadcaster.publicar(
            {
                **evento_lectura,
                "episodio_alerta": str(episodio_actual.id),
                "alerta_abierta": episodio_actual.episodio_abierto,
                "lectura_inicial_id": str(episodio_actual.lectura_inicial_id),
                "lectura_mas_reciente_id": str(episodio_actual.lectura_mas_reciente_id),
            },
            tipo_episodio,
        )


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.sse_broadcaster = SSEBroadcaster()

    if settings.mqtt_enabled and settings.environment != "test":

        async def manejador(client: aiomqtt.Client, message: aiomqtt.Message) -> None:
            await _procesar_mensaje_mqtt(message, app.state.sse_broadcaster, client)

        # La conexión y sus reintentos viven dentro de la tarea consumidora, así
        # que el arranque nunca se bloquea ni se cae por un broker inaccesible.
        # Se captura Exception y no solo MqttError a propósito: un fallo al
        # construir el cliente (configuración TLS, parámetros incompatibles con
        # la versión de aiomqtt) no es un MqttError y antes tumbaba el arranque
        # completo del backend en Railway.
        try:
            async with mqtt_session(settings, manejador) as tarea_mqtt:
                app.state.mqtt = tarea_mqtt
                yield
        except Exception as exc:
            logger.exception(
                "No se pudo iniciar la ingesta MQTT (%s). Backend continúa sin MQTT.", exc
            )
            app.state.mqtt = None
            yield
    else:
        app.state.mqtt = None
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    es_produccion = settings.environment == "production"

    app = FastAPI(
        title="IoT Cadena de Frío Farmacéutica - Backend",
        description=(
            "Prototipo IoT + IA con trazabilidad digital verificable para el monitoreo "
            "de la cadena de frío de medicamentos termolábiles en farmacias independientes."
        ),
        version="1.0.0",
        lifespan=lifespan,
        # La documentación interactiva no se expone en producción.
        docs_url=None if es_produccion else "/docs",
        redoc_url=None if es_produccion else "/redoc",
        openapi_url=None if es_produccion else "/openapi.json",
    )

    app.state.login_rate_limiter = SlidingWindowRateLimiter(
        max_intentos=settings.login_max_intentos,
        ventana_segundos=settings.login_ventana_segundos,
        max_claves=settings.security_state_max_entries,
    )
    # Revocación de access tokens (logout) y consumo único de tickets SSE.
    app.state.token_revocation = JtiStore(settings.security_state_max_entries)
    app.state.sse_ticket_store = JtiStore(settings.security_state_max_entries)

    if es_produccion:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    instalar_security_headers(app, hsts=es_produccion)
    instalar_proteccion_api(app, settings)

    app.include_router(auth_router)
    app.include_router(usuarios_router)
    app.include_router(lecturas_router)
    app.include_router(alertas_router)
    app.include_router(trazabilidad_router)
    app.include_router(reportes_router)
    app.include_router(auditoria_router)
    app.include_router(ia_router)
    app.include_router(sse_router)
    app.include_router(dispositivos_router)
    app.include_router(firmware_router)
    app.include_router(checklist_router)

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        # Liveness: NO consulta la base de datos a propósito. Si PostgreSQL cae,
        # Railway reiniciaría el backend en bucle sin arreglar nada; el estado de
        # las dependencias está en /health/detalle.
        return {"status": "ok", "timestamp": datetime.now(tz=timezone.utc).isoformat()}

    @app.get("/health/detalle", tags=["health"])
    async def health_detalle(
        request: Request,
        session: DbSessionDep,
        _admin=Depends(require_roles(Rol.ADMINISTRADOR)),
    ) -> dict:
        """HU-56: estado de las dependencias para el operador (solo administrador)."""
        try:
            await session.execute(text("SELECT 1"))
            db = "ok"
        except Exception as exc:  # pragma: no cover - depende de una BD caída
            db = f"error: {type(exc).__name__}"
        try:
            revision = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        except Exception:
            # Sin tabla alembic_version (BD creada con create_all en pruebas).
            await session.rollback()
            revision = None
        tarea_mqtt = getattr(request.app.state, "mqtt", None)
        despachador = getattr(request.app.state, "notificaciones", None)
        return {
            "status": "ok" if db == "ok" else "degradado",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "db": db,
            "alembic_revision": revision,
            "mqtt": {
                "habilitado": tarea_mqtt is not None,
                "tarea_activa": tarea_mqtt is not None and not tarea_mqtt.done(),
                "conectado": ESTADO_CONEXION.conectado,
                "desde": ESTADO_CONEXION.desde.isoformat() if ESTADO_CONEXION.desde else None,
            },
            "notificaciones": {
                "worker_activo": despachador is not None and despachador.activo,
                "cola_pendiente": despachador.pendientes if despachador is not None else 0,
            },
        }

    return app


app = create_app()
