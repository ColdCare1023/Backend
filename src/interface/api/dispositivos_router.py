import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.gestionar_dispositivos import (
    ActualizarResponsableDispositivoUseCase,
    ActualizarUbicacionYMetadatosInstalacionUseCase,
    ConsultarEstadoCalibracionUseCase,
    ConsultarHistorialConfiguracionUseCase,
    DarDeBajaDispositivoUseCase,
    RegistrarAltaDispositivoUseCase,
    RegistrarCalibracionUseCase,
    RevocarCredencialDispositivoUseCase,
    RotarCredencialDispositivoUseCase,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.value_objects.device_id import PATRON_DEVICE_ID
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.mqtt.broker_admin import BrokerAdmin, obtener_broker_admin
from src.interface.api.deps import DbSessionDep, SettingsDep, require_roles
from src.interface.api.schemas import (
    CalibracionRequest,
    CredencialDispositivoResponse,
    DispositivoAltaRequest,
    DispositivoBajaRequest,
    DispositivoResponse,
    DispositivoResumenResponse,
    EstadoOperativoResponse,
    HistorialConfiguracionResponse,
    IncidenteOperativoResponse,
    InstalacionDispositivoRequest,
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


@router.post("", response_model=DispositivoResponse, status_code=status.HTTP_201_CREATED)
async def dar_de_alta(
    body: DispositivoAltaRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
) -> DispositivoResponse:
    """HU-43 Escenario 1: alta explícita de un nodo nuevo — un device_id
    nunca se reutiliza, ni siquiera de un equipo dado de baja."""
    repositorio = SQLAlchemyDeviceRepository(session)
    use_case = RegistrarAltaDispositivoUseCase(
        repositorio, RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    try:
        dispositivo = await use_case.execute(
            body.device_id, body.nombre, body.ubicacion, body.sensores_habilitados, admin.id,
            es_demo=body.es_demo,
        )
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="ALTA_DISPOSITIVO",
        recurso=f"dispositivos/{body.device_id}",
        detalle={"nombre": body.nombre, "sensores_habilitados": body.sensores_habilitados, "es_demo": body.es_demo},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    return DispositivoResponse(**dispositivo)


# HU-33: cadencia de lectura del nodo y silencio tolerado antes de declarar
# SIN_DATOS (3 ciclos perdidos: una reconexión breve no dispara la alarma).
INTERVALO_REPORTE_SEGUNDOS = 30
UMBRAL_SIN_DATOS_SEGUNDOS = 3 * INTERVALO_REPORTE_SEGUNDOS


def _como_utc(instante: datetime | None) -> datetime | None:
    if instante is None:
        return None
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


def _estado_operativo(device: dict, ultima, ahora: datetime) -> EstadoOperativoResponse:
    """Clasifica el nodo por su última lectura recibida (no por la IA)."""
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
    if silencio is None or silencio >= UMBRAL_SIN_DATOS_SEGUNDOS:
        estado = "SIN_DATOS"
        detectado = (ultimo_reporte + timedelta(seconds=UMBRAL_SIN_DATOS_SEGUNDOS)) if ultimo_reporte else ahora
        incidencias.append(IncidenteOperativoResponse(
            id=f"sin_datos:{device['id']}:{int(detectado.timestamp())}",
            device_id=device["id"], tipo="sin_datos", estado="activo", detectado_en=detectado,
            ultimo_reporte_en=ultimo_reporte, umbral_silencio_segundos=UMBRAL_SIN_DATOS_SEGUNDOS,
        ))
    elif any(s.estado == "error" for s in sensores):
        estado = "FALLA_SENSOR"
    else:
        estado = "OPERATIVO"

    return EstadoOperativoResponse(
        **device,
        ultimo_reporte_en=ultimo_reporte,
        intervalo_reporte_segundos=INTERVALO_REPORTE_SEGUNDOS,
        umbral_sin_datos_segundos=UMBRAL_SIN_DATOS_SEGUNDOS,
        estado_operativo=estado,
        sensores=sensores,
        incidencias_operativas=incidencias,
    )


@router.get("/estado-operativo", response_model=list[EstadoOperativoResponse])
async def estado_operativo(
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
    todos: bool = Query(default=False, description="Incluir también los nodos sin incidencias"),
) -> list[EstadoOperativoResponse]:
    """HU-33: estado operativo de los nodos activos.

    Por defecto solo los que requieren atención (SIN_DATOS o FALLA_SENSOR), que
    es lo que el dashboard muestra como incidencias; con `todos=true`, todos los
    nodos activos (selector de dispositivo del dashboard).
    """
    ahora = datetime.now(timezone.utc)
    lecturas = SQLAlchemyLecturaRepository(session)
    resultado = []
    for device in await SQLAlchemyDeviceRepository(session).listar():
        if not device.get("activo", True):
            continue
        estado = _estado_operativo(device, await lecturas.ultima_recibida_por_device(device["id"]), ahora)
        if todos or estado.estado_operativo != "OPERATIVO":
            resultado.append(estado)
    return sorted(resultado, key=lambda e: e.id)


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
    if await broker.eliminar_credencial(device_id) is not True:
        logger.warning("Dispositivo %s dado de baja, pero su credencial no se eliminó del broker.", device_id)
    if await broker.desconectar_cliente(device_id) is not True:
        logger.warning("Dispositivo %s dado de baja, pero su sesión MQTT no se pudo expulsar.", device_id)
    return DispositivoResponse(**dispositivo)


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

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="ROTAR_CREDENCIAL_DISPOSITIVO",
        recurso=f"dispositivos/{device_id}/credenciales",
        detalle={"motivo": body.motivo},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    # BE-002: tras el COMMIT. Primero se aprovisiona el token nuevo en el broker
    # (autenticación integrada de EMQX) y luego se expulsa la sesión anterior,
    # para que la reconexión ya use la credencial nueva.
    aprovisionada = await broker.aprovisionar_credencial(device_id, token)
    if aprovisionada is not True:
        logger.warning("Credencial de %s rotada, pero no se aprovisionó en el broker.", device_id)
    cerrada = await broker.desconectar_cliente(device_id)
    mensaje = None
    if aprovisionada is None:
        mensaje = (
            f"Pendiente en el broker: cree o actualice el usuario «{device_id}» con este token en la "
            "autenticación de la instancia de EMQX (consola). El token no se mostrará de nuevo."
        )
    return CredencialDispositivoResponse(
        device_id=device_id, token=token, generado_en=datetime.now(tz=timezone.utc),
        credencial_aprovisionada=aprovisionada, mensaje_broker=mensaje, sesion_broker_cerrada=cerrada,
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

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="REVOCAR_CREDENCIAL_DISPOSITIVO",
        recurso=f"dispositivos/{device_id}/credenciales",
        detalle={"motivo": body.motivo},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    # BE-002: se elimina el usuario del broker y se cierra la sesión abierta;
    # sin esto el dispositivo (o quien tuviera su token) seguiría publicando
    # hasta la próxima reconexión.
    if await broker.eliminar_credencial(device_id) is not True:
        logger.warning("Credencial de %s revocada, pero no se eliminó del broker.", device_id)
    if await broker.desconectar_cliente(device_id) is not True:
        logger.warning("Credencial de %s revocada, pero su sesión MQTT no se pudo expulsar.", device_id)


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
