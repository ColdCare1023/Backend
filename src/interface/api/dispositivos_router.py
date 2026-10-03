from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

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
    VerificarCredencialDispositivoUseCase,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.api_protection import limitar_por_ip
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.schemas import (
    AutenticarDispositivoRequest,
    AutenticarDispositivoResponse,
    CalibracionRequest,
    CredencialDispositivoResponse,
    DispositivoAltaRequest,
    DispositivoBajaRequest,
    DispositivoResponse,
    HistorialConfiguracionResponse,
    InstalacionDispositivoRequest,
    ResponsableDispositivoRequest,
    RevocarCredencialRequest,
    RotarCredencialRequest,
)

router = APIRouter(prefix="/api/dispositivos", tags=["dispositivos"])

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
            body.device_id, body.nombre, body.ubicacion, body.sensores_habilitados, admin.id
        )
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="ALTA_DISPOSITIVO",
        recurso=f"dispositivos/{body.device_id}",
        detalle={"nombre": body.nombre, "sensores_habilitados": body.sensores_habilitados},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    return DispositivoResponse(**dispositivo)


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
    return {
        "vencidos": [DispositivoResponse(**d).model_dump(mode="json") for d in estado["vencidos"]],
        "proximos_a_vencer": [
            DispositivoResponse(**d).model_dump(mode="json") for d in estado["proximos_a_vencer"]
        ],
    }


@router.patch("/{device_id}/calibracion", response_model=DispositivoResponse)
async def registrar_calibracion(
    device_id: str,
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
    device_id: str,
    body: DispositivoBajaRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
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
        detalle={"motivo": body.motivo, "reemplazo": body.device_id_reemplazo},
        ip_origen=ip,
    )
    await session.commit()
    return DispositivoResponse(**dispositivo)


@router.post("/{device_id}/credenciales/rotar", response_model=CredencialDispositivoResponse)
async def rotar_credencial(
    device_id: str,
    body: RotarCredencialRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
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
    return CredencialDispositivoResponse(
        device_id=device_id, token=token, generado_en=datetime.now(tz=timezone.utc)
    )


@router.post("/{device_id}/credenciales/revocar", status_code=status.HTTP_204_NO_CONTENT)
async def revocar_credencial(
    device_id: str,
    body: RevocarCredencialRequest,
    session: DbSessionDep,
    request: Request,
    admin=Depends(router_dep),
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


@router.post(
    "/autenticar",
    response_model=AutenticarDispositivoResponse,
    dependencies=[limitar_por_ip("dispositivos_autenticar", 30, 60)],
)
async def autenticar_dispositivo(
    body: AutenticarDispositivoRequest, session: DbSessionDep
) -> AutenticarDispositivoResponse:
    """Backend de autenticación HTTP para EMQX Cloud (HU-10/HU-44): sin JWT
    de sesión a propósito — lo llama el broker, no un usuario. La cuota por
    IP acota el costo de un intento de fuerza bruta contra el hash."""
    use_case = VerificarCredencialDispositivoUseCase(SQLAlchemyDeviceRepository(session))
    autorizado = await use_case.execute(body.device_id, body.token)
    return AutenticarDispositivoResponse(autorizado=autorizado)


@router.patch("/{device_id}/instalacion", response_model=DispositivoResponse)
async def actualizar_instalacion(
    device_id: str,
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
    device_id: str,
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
    device_id: str,
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.ADMINISTRADOR, Rol.AUDITOR)),
) -> list[HistorialConfiguracionResponse]:
    """HU-49: historial auditable de cambios de configuración/instalación/
    calibración de un dispositivo, de solo lectura."""
    use_case = ConsultarHistorialConfiguracionUseCase(SQLAlchemyDeviceRepository(session))
    historial = await use_case.execute(device_id)
    return [HistorialConfiguracionResponse(**h) for h in historial]
