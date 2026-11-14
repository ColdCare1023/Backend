import csv
import io
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.interface.api.api_protection import limitar_por_usuario
from src.interface.api.deps import DbSessionDep, require_roles
from src.interface.api.schemas import AuditLogResponse

router = APIRouter(prefix="/api/auditoria", tags=["auditoria"])

ResultadoFiltro = Literal["exitoso", "fallido", "denegado", "bloqueado"]

# HU-50 esc. 4: lo que el panel y su exportación declaran sobre sí mismos.
ALCANCE_BITACORA = (
    "Supervisión y evidencia técnica de los controles registrados por el sistema. "
    "No constituye una certificación de seguridad ni la confirmación de un ataque."
)
# Tope de filas de una exportación (memoria de la instancia; se declara si se alcanza).
MAX_FILAS_EXPORTACION = 10_000
_COLUMNAS_CSV = ("fecha_utc", "id", "usuario_id", "accion", "resultado", "recurso", "ip_origen", "detalle")


@router.get("", response_model=list[AuditLogResponse])
async def listar_auditoria(
    session: DbSessionDep,
    # HU-42: "el auditor consulta la bitácora... obtiene una vista de solo
    # lectura" — antes solo ADMINISTRADOR podía leerla, contradiciendo el rol
    # narrativo de la propia historia.
    _usuario=Depends(require_roles(Rol.ADMINISTRADOR, Rol.AUDITOR)),
    limite: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    # HU-42 criterio 4 / HU-50: filtrar por periodo, actor, tipo de acción o resultado.
    desde: datetime | None = None,
    hasta: datetime | None = None,
    usuario_id: UUID | None = None,
    accion: str | None = None,
    resultado: ResultadoFiltro | None = None,
) -> list[AuditLogResponse]:
    repositorio = SQLAlchemyAuditLogRepository(session)
    registros = await repositorio.listar(
        limite=limite,
        offset=offset,
        desde=desde,
        hasta=hasta,
        usuario_id=usuario_id,
        accion=accion,
        resultado=resultado,
    )
    return [AuditLogResponse(**registro) for registro in registros]


def _celda(valor: object) -> str:
    """Texto seguro para hojas de cálculo: una celda que empieza por = + - @
    (o tabulador/retorno) se interpretaría como fórmula (inyección CSV)."""
    texto = "" if valor is None else str(valor)
    return f"'{texto}" if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


@router.get(
    "/exportar",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Bitácora filtrada en CSV"}},
    dependencies=[limitar_por_usuario("auditoria_exportar", 10, 60)],
)
async def exportar_auditoria(
    session: DbSessionDep,
    request: Request,
    usuario=Depends(require_roles(Rol.ADMINISTRADOR, Rol.AUDITOR)),
    desde: datetime | None = None,
    hasta: datetime | None = None,
    usuario_id: UUID | None = None,
    accion: str | None = None,
    resultado: ResultadoFiltro | None = None,
) -> Response:
    """HU-42 esc. 1 / HU-50 esc. 4: exporta la bitácora filtrada en CSV. La
    propia exportación es una operación sensible y queda auditada (quién, qué
    filtros y cuántas filas). El archivo declara su alcance en la primera línea."""
    repositorio = SQLAlchemyAuditLogRepository(session)
    filas = await repositorio.listar(
        limite=MAX_FILAS_EXPORTACION + 1,
        desde=desde,
        hasta=hasta,
        usuario_id=usuario_id,
        accion=accion,
        resultado=resultado,
    )
    truncado = len(filas) > MAX_FILAS_EXPORTACION
    filas = filas[:MAX_FILAS_EXPORTACION]

    salida = io.StringIO()
    salida.write(f"# ColdCare — bitácora de auditoría. {ALCANCE_BITACORA}\n")
    if truncado:
        salida.write(f"# Exportación recortada a las {MAX_FILAS_EXPORTACION} filas más recientes del filtro.\n")
    escritor = csv.writer(salida)
    escritor.writerow(_COLUMNAS_CSV)
    for fila in filas:
        creado = fila["created_at"]
        if creado.tzinfo is None:
            creado = creado.replace(tzinfo=timezone.utc)
        escritor.writerow(
            [
                creado.astimezone(timezone.utc).isoformat(),
                fila["id"],
                fila["usuario_id"] or "",
                _celda(fila["accion"]),
                fila["resultado"],
                _celda(fila["recurso"]),
                _celda(fila["ip_origen"]),
                _celda(json.dumps(fila["detalle"] or {}, ensure_ascii=False, sort_keys=True, default=str)),
            ]
        )

    filtros = {
        "desde": desde.isoformat() if desde else None,
        "hasta": hasta.isoformat() if hasta else None,
        "usuario_id": str(usuario_id) if usuario_id else None,
        "accion": accion,
        "resultado": resultado,
    }
    await AuditarAccionCriticaUseCase(repositorio, SQLAlchemyTrazabilidadRepository(session)).execute(
        usuario_id=usuario.id,
        accion="EXPORTAR_BITACORA",
        recurso="auditoria/exportar",
        detalle={"filtros": filtros, "filas": len(filas), "truncado": truncado},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()

    nombre = f"bitacora_{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    return Response(
        content=salida.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
