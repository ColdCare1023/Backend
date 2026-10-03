import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.exportar_reporte_bpa import ExportarReporteBPAUseCase
from src.application.use_cases.exportar_reporte_bpa_pdf import ExportarReporteBPAPDFUseCase
from src.application.use_cases.verificar_integridad_registro import (
    VerificarIntegridadRegistroUseCase,
)
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.checklist_repository import (
    SQLAlchemyChecklistRepository,
)
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.reporte_repository import SQLAlchemyReporteRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.api_protection import limitar_por_usuario
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.mappers import alerta_to_response, lectura_to_response, trazabilidad_to_response
from src.interface.api.schemas import ReporteBPAResponse

router = APIRouter(prefix="/api/reportes", tags=["reportes"])

# Un reporte BPA cubre como mucho un ejercicio anual. El techo no es cosmético:
# cada exportación materializa en memoria hasta 10.000 lecturas, 10.000 alertas
# y 10.000 registros de trazabilidad, y la instancia de Railway dispone de
# 512 MB compartidos con el resto de la aplicación.
MAX_DIAS_RANGO_REPORTE = 366


def _validar_rango(fecha_desde: datetime, fecha_hasta: datetime) -> None:
    if fecha_desde > fecha_hasta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="fecha_desde no puede ser posterior a fecha_hasta",
        )
    if (fecha_hasta - fecha_desde).days > MAX_DIAS_RANGO_REPORTE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El periodo del reporte no puede superar {MAX_DIAS_RANGO_REPORTE} días.",
        )


@router.get(
    "/bpa",
    response_model=ReporteBPAResponse,
    # Exportar es la operación más cara de la API (tres consultas amplias y una
    # serialización grande). Sin cuota propia, el límite global por IP la deja
    # repetir cientos de veces por minuto.
    dependencies=[limitar_por_usuario("reportes_bpa", 10, 60)],
)
async def exportar_reporte_bpa(
    fecha_desde: datetime,
    fecha_hasta: datetime,
    session: DbSessionDep,
    request: Request,
    usuario=Depends(require_roles(Rol.FARMACEUTICO, Rol.AUDITOR)),
    device_id: str | None = None,
) -> ReporteBPAResponse:
    _validar_rango(fecha_desde, fecha_hasta)

    lectura_repository = SQLAlchemyLecturaRepository(session)
    alerta_repository = SQLAlchemyAlertaRepository(session)
    trazabilidad_repository = SQLAlchemyTrazabilidadRepository(session)
    reporte_repository = SQLAlchemyReporteRepository(session)

    use_case = ExportarReporteBPAUseCase(
        lectura_repository, alerta_repository, trazabilidad_repository, reporte_repository
    )
    reporte = await use_case.execute(usuario.id, fecha_desde, fecha_hasta, device_id)

    auditoria_repository = SQLAlchemyAuditLogRepository(session)
    await AuditarAccionCriticaUseCase(auditoria_repository, trazabilidad_repository).execute(
        usuario_id=usuario.id,
        accion="EXPORTAR_REPORTE_BPA",
        recurso="reportes/bpa",
        detalle={
            "device_id": device_id,
            "fecha_desde": fecha_desde.isoformat(),
            "fecha_hasta": fecha_hasta.isoformat(),
        },
        ip_origen=request.client.host if request.client else None,
        device_id=device_id,
    )
    await session.commit()

    return ReporteBPAResponse(
        device_id=reporte.device_id,
        fecha_desde=reporte.fecha_desde,
        fecha_hasta=reporte.fecha_hasta,
        lecturas=[lectura_to_response(lectura) for lectura in reporte.lecturas],
        alertas=[alerta_to_response(a) for a in reporte.alertas],
        registros_trazabilidad=[trazabilidad_to_response(r) for r in reporte.registros_trazabilidad],
        truncado=reporte.truncado,
        lecturas_truncadas=reporte.lecturas_truncadas,
        alertas_truncadas=reporte.alertas_truncadas,
        trazabilidad_truncada=reporte.trazabilidad_truncada,
        limite_por_coleccion=reporte.limite_aplicado,
    )


@router.get(
    "/bpa/pdf",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/pdf": {}}, "description": "Reporte BPA en PDF"}},
    dependencies=[limitar_por_usuario("reportes_bpa_pdf", 10, 60)],
)
async def exportar_reporte_bpa_pdf(
    fecha_desde: datetime,
    fecha_hasta: datetime,
    session: DbSessionDep,
    request: Request,
    usuario=Depends(require_roles(Rol.FARMACEUTICO, Rol.ADMINISTRADOR, Rol.AUDITOR)),
    device_id: str | None = None,
) -> StreamingResponse:
    """RF-13 / HU-38: descarga el reporte BPA del período en PDF, incluyendo el
    veredicto de integridad de la cadena SHA-256 dentro del propio documento."""
    _validar_rango(fecha_desde, fecha_hasta)

    trazabilidad_repository = SQLAlchemyTrazabilidadRepository(session)
    # Verificación de SOLO LECTURA: se omiten a propósito el repositorio de
    # corrupción y el de hash encadenado. Con ellos, descargar un reporte
    # dispararía eventos de emergencia y snapshots forenses como efecto
    # colateral — la detección activa vive en GET /api/trazabilidad/verificar.
    verificar_integridad = VerificarIntegridadRegistroUseCase(trazabilidad_repository)

    use_case = ExportarReporteBPAPDFUseCase(
        SQLAlchemyLecturaRepository(session),
        SQLAlchemyAlertaRepository(session),
        trazabilidad_repository,
        SQLAlchemyReporteRepository(session),
        SQLAlchemyChecklistRepository(session),
        verificar_integridad,
    )
    pdf_bytes = await use_case.execute(
        usuario_id=usuario.id,
        usuario_nombre=usuario.nombre,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        device_id=device_id,
    )

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), trazabilidad_repository
    ).execute(
        usuario_id=usuario.id,
        accion="EXPORTAR_REPORTE_BPA_PDF",
        recurso="reportes/bpa/pdf",
        detalle={
            "device_id": device_id,
            "fecha_desde": fecha_desde.isoformat(),
            "fecha_hasta": fecha_hasta.isoformat(),
            "bytes": len(pdf_bytes),
        },
        ip_origen=request.client.host if request.client else None,
        device_id=device_id,
    )
    await session.commit()

    nombre = f"reporte_bpa_{fecha_desde.date().isoformat()}_{fecha_hasta.date().isoformat()}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            # El frontend descarga vía XHR: sin exponer la cabecera, no puede
            # leer el nombre de archivo sugerido por el servidor.
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
