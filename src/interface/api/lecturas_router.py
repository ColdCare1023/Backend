from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.consultar_historial_termico import ConsultarHistorialTermicoUseCase
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.exceptions import DispositivoNoAutorizadoError, LecturaInvalidaError
from src.domain.value_objects.rol import Rol
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.infrastructure.notifications.notificacion_service import NotificacionService
from src.interface.api.api_protection import limitar_ingesta_lecturas
from src.interface.api.deps import DbSessionDep, SettingsDep, require_roles
from src.interface.api.mappers import evidencia_edge, lectura_to_response
from src.interface.api.schemas import LecturaIngestRequest, LecturaResponse

router = APIRouter(prefix="/api/lecturas", tags=["lecturas"])


@router.post(
    "",
    response_model=LecturaResponse,
    status_code=status.HTTP_201_CREATED,
    # B13: cuota propia de la vía REST de ingesta. El volcado del buffer offline
    # del ESP32 va por MQTT (RF-06) y no la atraviesa, así que este techo no
    # afecta al RNF-07.
    dependencies=[limitar_ingesta_lecturas()],
)
async def ingestar_lectura(
    body: LecturaIngestRequest,
    session: DbSessionDep,
    settings: SettingsDep,
    request: Request,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO)),
) -> LecturaResponse:
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
        device_id=body.device_id,
        timestamp=body.timestamp,
        temperatura_ambiental=body.temperatura_ambiental,
        humedad_ambiental=body.humedad_ambiental,
        temperatura_interna=body.temperatura_interna,
        apertura_refrigerador=body.apertura_refrigerador,
        estado_conectividad=body.estado_conectividad,
        reading_id=body.reading_id,
        schema_version=body.schema_version,
        boot_id=body.boot_id,
        seq_no=body.seq_no,
        time_quality=body.time_quality,
        payload=evidencia_edge(
            firmware_version=body.firmware_version,
            duracion_apertura_segundos=body.duracion_apertura_segundos,
        ),
    )
    episodio_previo = await alerta_repository.obtener_episodio_abierto(body.device_id)
    try:
        lectura_guardada = await use_case.execute(lectura)
    except DispositivoNoAutorizadoError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LecturaInvalidaError as exc:
        # El rechazo por timestamp deja rastro en audit_logs dentro del caso de
        # uso; hay que confirmarlo para que sobreviva al 422.
        await session.commit()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    episodio_actual = await alerta_repository.obtener_episodio_abierto(body.device_id)
    await session.commit()

    respuesta = lectura_to_response(lectura_guardada)
    # B-06: la ingesta por HTTP también notifica al dashboard. Antes solo el
    # camino MQTT emitía SSE, así que una lectura enviada por REST se
    # persistía sin que ninguna pantalla abierta se enterara.
    await _emitir_eventos_sse(request, respuesta, lectura_guardada, episodio_previo, episodio_actual)
    return respuesta


async def _emitir_eventos_sse(
    request: Request,
    respuesta: LecturaResponse,
    lectura_guardada: LecturaTermica,
    episodio_previo,
    episodio_actual,
) -> None:
    """Misma semántica de eventos que el camino MQTT (ver interface/main.py):
    lectura / fallo_sensor / alerta / episodio_actualizado / recuperacion."""
    broadcaster = getattr(request.app.state, "sse_broadcaster", None)
    if broadcaster is None:
        return

    evento = respuesta.model_dump(mode="json")
    if lectura_guardada.estado_inferencia == "omitida":
        tipo = "fallo_sensor" if lectura_guardada.origen_clasificacion == "fallo_sensor" else "inferencia_omitida"
    else:
        tipo = "lectura"
    await broadcaster.publicar(evento, tipo)

    if lectura_guardada.nivel_riesgo is None:
        return
    if episodio_previo is not None and episodio_actual is None:
        await broadcaster.publicar(evento, "recuperacion")
    elif episodio_actual is not None:
        await broadcaster.publicar(
            {
                **evento,
                "episodio_alerta": str(episodio_actual.id),
                "alerta_abierta": episodio_actual.episodio_abierto,
                "lectura_inicial_id": str(episodio_actual.lectura_inicial_id),
                "lectura_mas_reciente_id": str(episodio_actual.lectura_mas_reciente_id),
            },
            "alerta" if episodio_previo is None else "episodio_actualizado",
        )


@router.get("", response_model=list[LecturaResponse])
async def listar_historial(
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
    device_id: str | None = None,
    nivel_riesgo: str | None = None,
    estado_conectividad: str | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    limite: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[LecturaResponse]:
    # HU-36 criterio 3: la validación de rango invertido no puede depender
    # solo del frontend — una llamada directa a la API también se rechaza.
    if desde is not None and hasta is not None and desde > hasta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="desde no puede ser posterior a hasta",
        )
    lectura_repository = SQLAlchemyLecturaRepository(session)
    use_case = ConsultarHistorialTermicoUseCase(lectura_repository)
    lecturas = await use_case.execute(
        device_id=device_id,
        nivel_riesgo=nivel_riesgo,
        estado_conectividad=estado_conectividad,
        desde=desde,
        hasta=hasta,
        limite=limite,
        offset=offset,
    )
    return [lectura_to_response(lectura) for lectura in lecturas]


@router.get("/{lectura_id}", response_model=LecturaResponse)
async def obtener_lectura(
    lectura_id: UUID,
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
) -> LecturaResponse:
    lectura_repository = SQLAlchemyLecturaRepository(session)
    lectura = await lectura_repository.obtener_por_id(lectura_id)
    if lectura is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lectura no encontrada")
    return lectura_to_response(lectura)
