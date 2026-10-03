from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.gestionar_corrupcion_cadena import AislarCorrupcionUseCase
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.application.use_cases.verificar_integridad_registro import (
    VerificarIntegridadPorDispositivoYPeriodoUseCase,
    VerificarIntegridadRegistroUseCase,
)
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.audit_log_repository import (
    SQLAlchemyAuditLogRepository,
)
from src.infrastructure.database.repositories.corrupcion_repository import SQLAlchemyCorrupcionRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.api_protection import limitar_por_usuario
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.mappers import trazabilidad_to_response
from src.interface.api.schemas import (
    DetalleInconsistenciaResponse,
    EstadoCadenaResponse,
    EstadoRegistroSegmentoResponse,
    TrazabilidadResponse,
    VerificacionIntegridadResponse,
    VerificacionSegmentoResponse,
)

router = APIRouter(prefix="/api/trazabilidad", tags=["trazabilidad"])


@router.get("", response_model=list[TrazabilidadResponse])
async def listar_trazabilidad(
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
    tipo_evento: str | None = None,
    device_id: str | None = None,
    chain_id: str | None = None,
    limite: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[TrazabilidadResponse]:
    repositorio = SQLAlchemyTrazabilidadRepository(session)
    registros = await repositorio.listar(
        tipo_evento=tipo_evento, device_id=device_id, chain_id=chain_id, limite=limite, offset=offset
    )
    return [trazabilidad_to_response(r) for r in registros]


@router.get(
    "/verificar",
    response_model=VerificacionIntegridadResponse,
    # B13: la verificación recorre y rehashea la cadena entera — es O(n) sobre
    # una tabla que solo crece. Sin cuota propia, un usuario autenticado podría
    # dejar la API sin CPU con un puñado de peticiones concurrentes. La clave es
    # el usuario y no la IP: en una farmacia todos salen por la misma.
    dependencies=[limitar_por_usuario("trazabilidad_verificar", 5, 60)],
)
async def verificar_integridad(
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
    chain_id: str | None = None,
) -> VerificacionIntegridadResponse:
    """HU-26 + HU-47 Escenarios 1-2: sin chain_id, verifica todas las cadenas
    a la vez (vista de administrador); con chain_id, verifica solo la cadena
    de esa unidad monitoreada (o "SISTEMA"), tal como exige el criterio de
    aceptación. Si detecta corrupción, además notifica (flag global, snapshot
    forense, evento de emergencia encadenado en la misma cadena afectada)."""
    repositorio = SQLAlchemyTrazabilidadRepository(session)
    use_case = VerificarIntegridadRegistroUseCase(
        repositorio,
        SQLAlchemyCorrupcionRepository(session),
        RegistrarHashEncadenadoUseCase(repositorio),
    )
    resultado = await use_case.execute(chain_id)
    await session.commit()
    detalle = None
    if resultado.detalle_inconsistencia is not None:
        d = resultado.detalle_inconsistencia
        detalle = DetalleInconsistenciaResponse(
            id=d.id, tipo_evento=d.tipo_evento, timestamp=d.timestamp,
            hash_esperado=d.hash_esperado, hash_almacenado=d.hash_almacenado, mensaje=d.mensaje,
        )
    return VerificacionIntegridadResponse(
        integra=resultado.integra,
        total_registros=resultado.total_registros,
        primer_registro_inconsistente=resultado.primer_registro_inconsistente,
        detalle_inconsistencia=detalle,
        registros_posteriores_afectados=resultado.registros_posteriores_afectados,
    )


@router.get(
    "/verificar-dispositivo",
    response_model=VerificacionSegmentoResponse,
    dependencies=[limitar_por_usuario("trazabilidad_verificar_dispositivo", 10, 60)],
)
async def verificar_integridad_por_dispositivo(
    session: DbSessionDep,
    request: Request,
    device_id: str,
    desde: datetime,
    hasta: datetime,
    usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
) -> VerificacionSegmentoResponse:
    """HU-37: verificación de integridad acotada a un dispositivo y periodo
    (a diferencia de /verificar, que recorre la cadena global completa)."""
    if desde > hasta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="desde no puede ser posterior a hasta",
        )
    repositorio = SQLAlchemyTrazabilidadRepository(session)
    use_case = VerificarIntegridadPorDispositivoYPeriodoUseCase(repositorio)
    resultado = await use_case.execute(device_id, desde, hasta)

    # HU-37 criterio 3: constancia verificable de la revisión, append-only,
    # encadenada — no solo el resultado devuelto al usuario que la pidió.
    await RegistrarHashEncadenadoUseCase(repositorio).execute(
        tipo_evento="VERIFICACION_INTEGRIDAD",
        payload={
            "device_id": device_id,
            "desde": desde.isoformat(),
            "hasta": hasta.isoformat(),
            "integra": resultado.integra,
            "total_bloques_verificados": resultado.total_bloques_verificados,
        },
        device_id=device_id,
        usuario_id=usuario.id,
    )
    await session.commit()

    return VerificacionSegmentoResponse(
        device_id=resultado.device_id,
        desde=resultado.desde,
        hasta=resultado.hasta,
        integra=resultado.integra,
        total_bloques_verificados=resultado.total_bloques_verificados,
        registros_del_dispositivo=[
            EstadoRegistroSegmentoResponse(
                id=r.id, tipo_evento=r.tipo_evento, timestamp=r.timestamp, integro=r.integro
            )
            for r in resultado.registros_del_dispositivo
        ],
        primer_registro_inconsistente=resultado.primer_registro_inconsistente,
    )


@router.get("/estado", response_model=EstadoCadenaResponse)
async def obtener_estado_cadena(
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
) -> EstadoCadenaResponse:
    """HU-47 Escenario 2: banner de advertencia en dashboards mientras cadena_comprometida=true."""
    comprometida = await SQLAlchemyCorrupcionRepository(session).cadena_comprometida()
    return EstadoCadenaResponse(cadena_comprometida=comprometida)


@router.post("/corrupcion/{registro_id}/aislar", status_code=204)
async def aislar_corrupcion(
    registro_id: UUID,
    session: DbSessionDep,
    request: Request,
    admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> None:
    """HU-47 Escenario 4, Opción 2 (Quarantine)."""
    repositorio = SQLAlchemyTrazabilidadRepository(session)
    use_case = AislarCorrupcionUseCase(
        repositorio,
        SQLAlchemyCorrupcionRepository(session),
        RegistrarHashEncadenadoUseCase(repositorio),
    )
    await use_case.execute(registro_id, usuario_id=admin.id)

    # RF-16: dar por rota la evidencia y arrancar una cadena nueva es la
    # intervención manual más sensible del sistema. Antes solo dejaba rastro en
    # la propia cadena y sin autor, así que la bitácora no podía responder
    # quién puso la evidencia en cuarentena ni desde dónde.
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="CADENA_CORRUPCION_AISLADA",
        recurso=f"trazabilidad/corrupcion/{registro_id}",
        detalle={"registro_corrupto_id": str(registro_id)},
        ip_origen=request.client.host if request.client else "desconocida",
    )
    await session.commit()
