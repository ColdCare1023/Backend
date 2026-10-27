import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.gestionar_dispositivos import (
    OPERACION_BROKER_APROVISIONAR,
    OPERACION_BROKER_RETIRAR,
    ActualizarIntervaloReporteUseCase,
    ActualizarResponsableDispositivoUseCase,
    ActualizarUbicacionYMetadatosInstalacionUseCase,
    ConfirmarOperacionBrokerUseCase,
    ConsultarEstadoCalibracionUseCase,
    ConsultarHistorialConfiguracionUseCase,
    DarDeBajaDispositivoUseCase,
    RegistrarAltaDispositivoUseCase,
    RegistrarCalibracionUseCase,
    RegistrarResultadoBrokerUseCase,
    RevocarCredencialDispositivoUseCase,
    RotarCredencialDispositivoUseCase,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.application.use_cases.vigilar_sin_datos import umbral_sin_datos
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_incidente_repository import IncidenteOperativo
from src.domain.value_objects.device_id import PATRON_DEVICE_ID
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.incidente_repository import SQLAlchemyIncidenteRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.mqtt.broker_admin import BrokerAdmin, obtener_broker_admin
from src.interface.api.deps import DbSessionDep, SettingsDep, require_roles
from src.interface.api.schemas import (
    CalibracionRequest,
    ConfirmarBrokerRequest,
    CredencialDispositivoResponse,
    DispositivoAltaRequest,
    DispositivoAltaResponse,
    DispositivoBajaRequest,
    DispositivoResponse,
    DispositivoResumenResponse,
    EstadoOperativoResponse,
    HistorialConfiguracionResponse,
    IncidenteOperativoResponse,
    InstalacionDispositivoRequest,
    IntervaloReporteRequest,
    ResponsableDispositivoRequest,
    RevocarCredencialRequest,
    RotarCredencialRequest,
    SensorIoTResponse,
)

router = APIRouter(prefix="/api/dispositivos", tags=["dispositivos"])

# BE-017: el segmento de ruta cumple el mismo patrón que el contrato del nodo.
DeviceIdRuta = Annotated[str, Path(pattern=PATRON_DEVICE_ID)]
logger = logging.getLogger("interface.api.dispositivos")


def get_broker_admin(settings: SettingsDep) -> BrokerAdmin:
    return obtener_broker_admin(settings)


def _motivo_pendiente(broker: BrokerAdmin, resultado: bool | None) -> str:
    if resultado is False:
        return "api_broker_error"
    return "modo_manual" if broker.configurado else "api_broker_sin_configurar"


async def _registrar_resultado_broker(
    session,
    broker: BrokerAdmin,
    device_id: str,
    operacion: str,
    resultado: bool | None,
    actor_id,
    ip: str | None,
) -> bool:
    """HU-44 Escenario 3: tras el COMMIT de la operación, deja persistente (y
    auditado) lo que el broker no confirmó. Devuelve True si quedó pendiente."""
    motivo = _motivo_pendiente(broker, resultado)
    pendiente = await RegistrarResultadoBrokerUseCase(
        SQLAlchemyDeviceRepository(session), RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    ).execute(device_id, operacion, resultado, motivo, actor_id)
    if pendiente:
        await SQLAlchemyAuditLogRepository(session).registrar(
            usuario_id=actor_id,
            accion="BROKER_OPERACION_PENDIENTE",
            recurso=f"dispositivos/{device_id}/broker",
            detalle={"operacion": operacion, "motivo": motivo},
            ip_origen=ip,
        )
    await session.commit()
    return pendiente


def _mensaje_aprovisionar(device_id: str, resultado: bool | None) -> str | None:
    if resultado is True:
        return None
    causa = (
        "La API del broker no aceptó la credencial: revise la instancia y"
        if resultado is False
        else "Pendiente en el broker:"
    )
    return (
        f"{causa} cree o actualice el usuario «{device_id}» con este token en la autenticación de la "
        "instancia de EMQX (consola) y confirme la operación en el sistema. A continuación cárguelo en el "
        "nodo con `nvs set mqtt_token` por la consola serie. Hasta hacerlo el nodo no conecta (sus "
        "lecturas quedan en LittleFS). El token no se mostrará de nuevo."
    )

# HU-43 Escenario 4: la HU pide "ADMINISTRADOR o TÉCNICO con permiso especial".
# Este backend no modela permisos granulares por usuario (solo RBAC por rol,
# ver rbac.py), así que se restringe a ADMINISTRADOR — documentado como
# simplificación deliberada en 08_hu43_47_ota_y_cierre.md.
router_dep = require_roles(Rol.ADMINISTRADOR)

# HU-30: la calibración la certifica el responsable técnico farmacéutico, no
# solo el administrador del sistema.
_roles_calibracion = require_roles(Rol.FARMACEUTICO, Rol.ADMINISTRADOR)


@router.get("", response_model=list[DispositivoResponse])
async def listar_dispositivos(session: DbSessionDep, _admin=Depends(router_dep)) -> list[DispositivoResponse]:
    repositorio = SQLAlchemyDeviceRepository(session)
    return [DispositivoResponse(**d) for d in await repositorio.listar()]


@router.post("", response_model=DispositivoAltaResponse, status_code=status.HTTP_201_CREATED)
async def dar_de_alta(
    body: DispositivoAltaRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
    broker: BrokerAdmin = Depends(get_broker_admin),
) -> DispositivoAltaResponse:
    """HU-43 Escenario 1: alta explícita de un nodo nuevo — un device_id
    nunca se reutiliza, ni siquiera de un equipo dado de baja.

    El alta emite la credencial MQTT del nodo (criterio de HU-43): la consola
    del broker se configura una sola vez con un token ya conocido, sin pasar
    por una primera «rotación». El token en claro solo viaja en esta respuesta."""
    repositorio = SQLAlchemyDeviceRepository(session)
    registrar_hash = RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    use_case = RegistrarAltaDispositivoUseCase(repositorio, registrar_hash)
    try:
        await use_case.execute(
            body.device_id, body.nombre, body.ubicacion, body.sensores_habilitados, admin.id,
            es_demo=body.es_demo,
        )
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    token = await RotarCredencialDispositivoUseCase(repositorio, registrar_hash).execute(
        body.device_id, admin.id, "emision_en_el_alta", tipo_evento="EMISION_CREDENCIAL_DISPOSITIVO"
    )

    ip = request.client.host if request.client else None
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="ALTA_DISPOSITIVO",
        recurso=f"dispositivos/{body.device_id}",
        detalle={
            "nombre": body.nombre, "sensores_habilitados": body.sensores_habilitados, "es_demo": body.es_demo,
            "credencial_emitida": True,
        },
        ip_origen=ip,
    )
    await session.commit()
    generado_en = datetime.now(tz=timezone.utc)
    # BE-002: tras el COMMIT, igual que en la rotación.
    aprovisionada = await broker.aprovisionar_credencial(body.device_id, token)
    await _registrar_resultado_broker(
        session, broker, body.device_id, OPERACION_BROKER_APROVISIONAR, aprovisionada, admin.id, ip
    )
    dispositivo = await repositorio.obtener(body.device_id)
    return DispositivoAltaResponse(
        **dispositivo,
        credencial=CredencialDispositivoResponse(
            device_id=body.device_id, token=token, generado_en=generado_en,
            credencial_aprovisionada=aprovisionada,
            mensaje_broker=_mensaje_aprovisionar(body.device_id, aprovisionada),
            sesion_broker_cerrada=None,
        ),
    )


def _como_utc(instante: datetime | None) -> datetime | None:
    if instante is None:
        return None
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


def _incidente_response(incidente: IncidenteOperativo) -> IncidenteOperativoResponse:
    return IncidenteOperativoResponse(
        id=str(incidente.id),
        device_id=incidente.device_id,
        tipo="sin_datos",
        estado="activo" if incidente.activo else "resuelto",
        detectado_en=_como_utc(incidente.abierto_en),
        ultimo_reporte_en=_como_utc(incidente.ultimo_reporte_en),
        umbral_silencio_segundos=incidente.umbral_segundos,
        recuperado_en=_como_utc(incidente.resuelto_en),
        notificado_en=_como_utc(incidente.notificado_en),
    )


def _estado_operativo(
    device: dict, ultima, ahora: datetime, abierto: IncidenteOperativo | None, n_intervalos: int
) -> EstadoOperativoResponse:
    """Clasifica el nodo por su última lectura recibida (no por la IA).

    HU-54: el umbral es N x el intervalo de captura del propio nodo, y el
    incidente SIN_DATOS es el persistente que abre la tarea periódica."""
    intervalo = device.get("intervalo_reporte_segundos") or 30
    umbral = umbral_sin_datos(intervalo, n_intervalos)
    ultimo_reporte = _como_utc(ultima.received_at or ultima.timestamp) if ultima else None
    sensores: list[SensorIoTResponse] = []
    if ultima is not None:
        sensores = [
            SensorIoTResponse(modelo="DS18B20", campo_telemetria="temperatura_interna",
                              estado="ok" if ultima.temperatura_interna is not None else "error"),
            SensorIoTResponse(
                modelo="SHT31", campo_telemetria="temperatura_ambiental",
                estado="ok" if ultima.temperatura_ambiental is not None and ultima.humedad_ambiental is not None
                else "error",
            ),
            SensorIoTResponse(modelo="MC-38", campo_telemetria="apertura_refrigerador",
                              estado="no_instalado" if ultima.apertura_refrigerador is None else "ok"),
        ]

    incidencias: list[IncidenteOperativoResponse] = []
    silencio = (ahora - ultimo_reporte).total_seconds() if ultimo_reporte else None
    if abierto is not None:
        estado = "SIN_DATOS"
        incidencias.append(_incidente_response(abierto))
    elif silencio is None or silencio >= umbral:
        # Silencio aún no registrado por la tarea periódica (o nodo que nunca
        # reportó): se informa igual, con un id calculado.
        estado = "SIN_DATOS"
        detectado = (ultimo_reporte + timedelta(seconds=umbral)) if ultimo_reporte else ahora
        incidencias.append(IncidenteOperativoResponse(
            id=f"sin_datos:{device['id']}:{int(detectado.timestamp())}",
            device_id=device["id"], tipo="sin_datos", estado="activo", detectado_en=detectado,
            ultimo_reporte_en=ultimo_reporte, umbral_silencio_segundos=umbral,
        ))
    elif any(s.estado == "error" for s in sensores):
        estado = "FALLA_SENSOR"
    else:
        estado = "OPERATIVO"

    return EstadoOperativoResponse(
        **{k: v for k, v in device.items() if k not in ("intervalo_reporte_segundos",)},
        ultimo_reporte_en=ultimo_reporte,
        intervalo_reporte_segundos=intervalo,
        umbral_sin_datos_segundos=umbral,
        estado_operativo=estado,
        sensores=sensores,
        incidencias_operativas=incidencias,
    )


@router.get("/estado-operativo", response_model=list[EstadoOperativoResponse])
async def estado_operativo(
    session: DbSessionDep,
    settings: SettingsDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
    todos: bool = Query(default=False, description="Incluir también los nodos sin incidencias"),
) -> list[EstadoOperativoResponse]:
    """HU-33/HU-54: estado operativo de los nodos activos.

    Por defecto solo los que requieren atención (SIN_DATOS o FALLA_SENSOR), que
    es lo que el dashboard muestra como incidencias; con `todos=true`, todos los
    nodos activos (selector de dispositivo del dashboard).
    """
    ahora = datetime.now(timezone.utc)
    lecturas = SQLAlchemyLecturaRepository(session)
    incidentes = SQLAlchemyIncidenteRepository(session)
    resultado = []
    for device in await SQLAlchemyDeviceRepository(session).listar():
        if not device.get("activo", True):
            continue
        estado = _estado_operativo(
            device, await lecturas.ultima_recibida_por_device(device["id"]), ahora,
            await incidentes.abierto(device["id"]), settings.sin_datos_n_intervalos,
        )
        if todos or estado.estado_operativo != "OPERATIVO":
            resultado.append(estado)
    return sorted(resultado, key=lambda e: e.id)


@router.get("/incidentes", response_model=list[IncidenteOperativoResponse])
async def listar_incidentes_operativos(
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR, Rol.ADMINISTRADOR)),
    device_id: Annotated[str | None, Query(pattern=PATRON_DEVICE_ID)] = None,
    limite: int = Query(default=100, ge=1, le=500),
) -> list[IncidenteOperativoResponse]:
    """HU-54: historial de incidentes SIN_DATOS (apertura, aviso y recuperación),
    separado de las alertas térmicas. Se declara ANTES de `/{device_id}`."""
    incidentes = await SQLAlchemyIncidenteRepository(session).listar(device_id=device_id, limite=limite)
    return [_incidente_response(i) for i in incidentes]


@router.get("/calibracion/estado")
async def consultar_estado_calibracion(
    session: DbSessionDep,
    _usuario=Depends(_roles_calibracion),
    dias_preaviso: int = Query(default=30, ge=1, le=365),
) -> dict:
    """HU-30: dispositivos con certificado vencido o próximo a vencer.

    Se declara ANTES de `/{device_id}` para que "calibracion" no se capture
    como un device_id."""
    use_case = ConsultarEstadoCalibracionUseCase(SQLAlchemyDeviceRepository(session))
    estado = await use_case.execute(datetime.now(tz=timezone.utc).date(), dias_preaviso)
    # BE-013: vista resumida (sin contacto del responsable ni credenciales).
    return {
        "vencidos": [DispositivoResumenResponse(**d).model_dump(mode="json") for d in estado["vencidos"]],
        "proximos_a_vencer": [
            DispositivoResumenResponse(**d).model_dump(mode="json") for d in estado["proximos_a_vencer"]
        ],
    }


@router.patch("/{device_id}/calibracion", response_model=DispositivoResponse)
async def registrar_calibracion(
    device_id: DeviceIdRuta,
    body: CalibracionRequest,
    session: DbSessionDep,
    request: Request,
    usuario=Depends(_roles_calibracion),
) -> DispositivoResponse:
    """HU-30: registra el certificado de calibración del sensor y lo ancla a
    la cadena de trazabilidad SHA-256."""
    use_case = RegistrarCalibracionUseCase(
        SQLAlchemyDeviceRepository(session),
        RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)),
    )
    try:
        dispositivo = await use_case.execute(
            device_id=device_id,
            fecha_calibracion=body.fecha_calibracion,
            numero_certificado=body.numero_certificado,
            observaciones=body.observaciones,
            meses_vigencia=body.meses_vigencia,
            usuario_id=usuario.id,
        )
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=usuario.id,
        accion="CALIBRACION_REGISTRADA",
        recurso=f"dispositivos/{device_id}/calibracion",
        detalle={
            "numero_certificado": body.numero_certificado,
            "fecha_calibracion": body.fecha_calibracion.isoformat(),
        },
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    return DispositivoResponse(**dispositivo)


@router.post("/{device_id}/baja", response_model=DispositivoResponse)
async def dar_de_baja(
    device_id: DeviceIdRuta,
    body: DispositivoBajaRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
    broker: BrokerAdmin = Depends(get_broker_admin),
) -> DispositivoResponse:
    repositorio = SQLAlchemyDeviceRepository(session)
    use_case = DarDeBajaDispositivoUseCase(
        repositorio, RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    try:
        dispositivo = await use_case.execute(
            device_id, body.motivo, body.descripcion, body.device_id_reemplazo
        )
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    ip = request.client.host if request.client else None
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="BAJA_DISPOSITIVO",
        recurso=f"dispositivos/{device_id}",
        detalle={"motivo": body.motivo, "reemplazo": body.device_id_reemplazo, "credencial_revocada": True},
        ip_origen=ip,
    )
    await session.commit()
    # BE-003: la baja revoca la credencial (se elimina del broker) y cierra la sesión abierta.
    eliminada = await broker.eliminar_credencial(device_id)
    if eliminada is not True:
        logger.warning("Dispositivo %s dado de baja, pero su credencial no se eliminó del broker.", device_id)
    if await broker.desconectar_cliente(device_id) is not True:
        logger.warning("Dispositivo %s dado de baja, pero su sesión MQTT no se pudo expulsar.", device_id)
    await _registrar_resultado_broker(session, broker, device_id, OPERACION_BROKER_RETIRAR, eliminada, admin.id, ip)
    return DispositivoResponse(**(await repositorio.obtener(device_id) or dispositivo))


@router.post("/{device_id}/credenciales/rotar", response_model=CredencialDispositivoResponse)
async def rotar_credencial(
    device_id: DeviceIdRuta,
    body: RotarCredencialRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
    broker: BrokerAdmin = Depends(get_broker_admin),
) -> CredencialDispositivoResponse:
    """HU-44 Escenario 2: genera y aprovisiona un nuevo token MQTT. El valor
    en claro solo se devuelve en ESTA respuesta — el backend únicamente
    conserva su hash a partir de aquí."""
    repositorio = SQLAlchemyDeviceRepository(session)
    use_case = RotarCredencialDispositivoUseCase(
        repositorio, RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    try:
        token = await use_case.execute(device_id, admin.id, body.motivo)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    ip = request.client.host if request.client else None
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="ROTAR_CREDENCIAL_DISPOSITIVO",
        recurso=f"dispositivos/{device_id}/credenciales",
        detalle={"motivo": body.motivo},
        ip_origen=ip,
    )
    await session.commit()
    # BE-002: tras el COMMIT. Primero se aprovisiona el token nuevo en el broker
    # (autenticación integrada de EMQX) y luego se expulsa la sesión anterior,
    # para que la reconexión ya use la credencial nueva.
    aprovisionada = await broker.aprovisionar_credencial(device_id, token)
    if aprovisionada is not True:
        logger.warning("Credencial de %s rotada, pero no se aprovisionó en el broker.", device_id)
    cerrada = await broker.desconectar_cliente(device_id)
    await _registrar_resultado_broker(
        session, broker, device_id, OPERACION_BROKER_APROVISIONAR, aprovisionada, admin.id, ip
    )
    return CredencialDispositivoResponse(
        device_id=device_id, token=token, generado_en=datetime.now(tz=timezone.utc),
        credencial_aprovisionada=aprovisionada, mensaje_broker=_mensaje_aprovisionar(device_id, aprovisionada),
        sesion_broker_cerrada=cerrada,
    )


@router.post("/{device_id}/credenciales/revocar", status_code=status.HTTP_204_NO_CONTENT)
async def revocar_credencial(
    device_id: DeviceIdRuta,
    body: RevocarCredencialRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
    broker: BrokerAdmin = Depends(get_broker_admin),
) -> None:
    """HU-44 Escenario 1: invalida de inmediato la credencial de un
    dispositivo cuyas credenciales se sospechan comprometidas."""
    repositorio = SQLAlchemyDeviceRepository(session)
    use_case = RevocarCredencialDispositivoUseCase(
        repositorio, RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    try:
        await use_case.execute(device_id, admin.id, body.motivo)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    ip = request.client.host if request.client else None
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="REVOCAR_CREDENCIAL_DISPOSITIVO",
        recurso=f"dispositivos/{device_id}/credenciales",
        detalle={"motivo": body.motivo},
        ip_origen=ip,
    )
    await session.commit()
    # BE-002: se elimina el usuario del broker y se cierra la sesión abierta;
    # sin esto el dispositivo (o quien tuviera su token) seguiría publicando
    # hasta la próxima reconexión. Mientras el usuario siga vivo en el broker
    # (modo manual), la ingesta rechaza y expulsa al nodo en cada intento.
    eliminada = await broker.eliminar_credencial(device_id)
    if eliminada is not True:
        logger.warning("Credencial de %s revocada, pero no se eliminó del broker.", device_id)
    if await broker.desconectar_cliente(device_id) is not True:
        logger.warning("Credencial de %s revocada, pero su sesión MQTT no se pudo expulsar.", device_id)
    await _registrar_resultado_broker(session, broker, device_id, OPERACION_BROKER_RETIRAR, eliminada, admin.id, ip)


@router.post("/{device_id}/broker/confirmar", response_model=DispositivoResponse)
async def confirmar_operacion_broker(
    device_id: DeviceIdRuta,
    body: ConfirmarBrokerRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
) -> DispositivoResponse:
    """HU-43/HU-44: el administrador confirma que hizo en la consola del broker
    la operación pendiente (crear/actualizar o retirar el usuario del nodo)."""
    use_case = ConfirmarOperacionBrokerUseCase(
        SQLAlchemyDeviceRepository(session), RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    try:
        dispositivo, operacion = await use_case.execute(device_id, admin.id, body.nota)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await SQLAlchemyAuditLogRepository(session).registrar(
        usuario_id=admin.id,
        accion="BROKER_CONSOLA_CONFIRMADA",
        recurso=f"dispositivos/{device_id}/broker",
        detalle={"operacion": operacion, "nota": body.nota},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    return DispositivoResponse(**dispositivo)


@router.patch("/{device_id}/intervalo", response_model=DispositivoResponse)
async def actualizar_intervalo_reporte(
    device_id: DeviceIdRuta,
    body: IntervaloReporteRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
) -> DispositivoResponse:
    """HU-54: fija el intervalo de captura del nodo (si el firmware no lo
    informa en `estado_nodo`). El umbral SIN_DATOS se recalcula con él."""
    repositorio = SQLAlchemyDeviceRepository(session)
    try:
        actualizado = await ActualizarIntervaloReporteUseCase(repositorio).execute(
            device_id, body.segundos, admin.id, "administrador"
        )
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if actualizado is not None:
        await SQLAlchemyAuditLogRepository(session).registrar(
            usuario_id=admin.id,
            accion="INTERVALO_REPORTE_ACTUALIZADO",
            recurso=f"dispositivos/{device_id}/intervalo",
            detalle={"segundos": body.segundos},
            ip_origen=request.client.host if request.client else None,
        )
        await session.commit()
    return DispositivoResponse(**(actualizado or await repositorio.obtener(device_id)))


@router.patch("/{device_id}/instalacion", response_model=DispositivoResponse)
async def actualizar_instalacion(
    device_id: DeviceIdRuta,
    body: InstalacionDispositivoRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
) -> DispositivoResponse:
    """HU-51: ubicación física del DS18B20 y metadatos de instalación. Un
    cambio de ubicación queda como evento histórico (HU-49), no como
    sobrescritura silenciosa."""
    repositorio = SQLAlchemyDeviceRepository(session)
    use_case = ActualizarUbicacionYMetadatosInstalacionUseCase(
        repositorio, RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    try:
        dispositivo = await use_case.execute(
            device_id, body.ubicacion, body.fecha_instalacion, admin.id, body.observaciones
        )
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="ACTUALIZAR_INSTALACION_DISPOSITIVO",
        recurso=f"dispositivos/{device_id}/instalacion",
        detalle={"ubicacion": body.ubicacion},
        ip_origen=request.client.host if request.client else None,
        device_id=device_id,
    )
    await session.commit()
    return DispositivoResponse(**dispositivo)


@router.patch("/{device_id}/responsable", response_model=DispositivoResponse)
async def actualizar_responsable(
    device_id: DeviceIdRuta,
    body: ResponsableDispositivoRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
) -> DispositivoResponse:
    """HU-53/HU-54: destinatario de las notificaciones de excursión crítica
    de este dispositivo por correo y SMS, en vez del destinatario único
    global. Un cambio queda como evento histórico (HU-49)."""
    repositorio = SQLAlchemyDeviceRepository(session)
    use_case = ActualizarResponsableDispositivoUseCase(
        repositorio, RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    try:
        dispositivo = await use_case.execute(
            device_id, body.nombre, body.email, body.telefono, admin.id
        )
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="ACTUALIZAR_RESPONSABLE_DISPOSITIVO",
        recurso=f"dispositivos/{device_id}/responsable",
        detalle={"responsable_nombre": body.nombre},
        ip_origen=request.client.host if request.client else None,
        device_id=device_id,
    )
    await session.commit()
    return DispositivoResponse(**dispositivo)


@router.get("/{device_id}/historial-configuracion", response_model=list[HistorialConfiguracionResponse])
async def historial_configuracion(
    device_id: DeviceIdRuta,
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.ADMINISTRADOR, Rol.AUDITOR)),
) -> list[HistorialConfiguracionResponse]:
    """HU-49: historial auditable de cambios de configuración/instalación/
    calibración de un dispositivo, de solo lectura."""
    use_case = ConsultarHistorialConfiguracionUseCase(SQLAlchemyDeviceRepository(session))
    historial = await use_case.execute(device_id)
    return [HistorialConfiguracionResponse(**h) for h in historial]
