from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.consultar_historial_termico import ConsultarHistorialTermicoUseCase
from src.application.use_cases.consultar_serie_termica import (
    ConsultarSerieTermicaUseCase,
    RangoSerieInvalidoError,
)
from src.application.use_cases.registrar_lectura_termica import RegistrarLecturaTermicaUseCase
from src.domain.entities.lectura_termica import ORIGEN_MANUAL, LecturaTermica
from src.domain.exceptions import DispositivoNoAutorizadoError, LecturaInvalidaError
from src.domain.value_objects.device_id import PATRON_DEVICE_ID
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.domain.value_objects.rol import Rol
from src.infrastructure.ai import gobierno
from src.infrastructure.ai.random_forest_service import get_random_forest_service
from src.infrastructure.database.repositories.alerta_repository import SQLAlchemyAlertaRepository
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.device_repository import SQLAlchemyDeviceRepository
from src.infrastructure.database.repositories.estado_nodo_repository import SQLAlchemyEstadoNodoRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.api_protection import limitar_ingesta_lecturas
from src.interface.api.deps import DbSessionDep, SettingsDep, require_roles
from src.interface.api.mappers import lectura_to_response, serie_to_response, tipo_sse_lectura
from src.interface.api.schemas import LecturaIngestRequest, LecturaResponse, SerieTermicaResponse

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
    usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO)),
) -> LecturaResponse:
    """BE-001: registro MANUAL de una lectura (por ejemplo, la de un
    termómetro de respaldo mientras el sensor del nodo está caído).

    No es una vía de telemetría: la lectura queda con `origen="manual"`, con
    su autor en la tabla, en el eslabón de la cadena y en la bitácora, con
    identificador generado por el servidor y una ventana temporal corta. Nunca
    ocupa la identidad de una lectura del dispositivo."""
    lectura_repository = SQLAlchemyLecturaRepository(session)
    alerta_repository = SQLAlchemyAlertaRepository(session)
    trazabilidad_repository = SQLAlchemyTrazabilidadRepository(session)
    # HU-46: el legado solo decide si es la versión activa y aprobada del registro.
    modo_legado = gobierno.modo_legado_efectivo(settings)
    clasificar_use_case = ClasificarRiesgoTermicoUseCase(get_random_forest_service(), modo_legado)

    use_case = RegistrarLecturaTermicaUseCase(
        lectura_repository,
        alerta_repository,
        trazabilidad_repository,
        clasificar_use_case,
        device_repository=SQLAlchemyDeviceRepository(session),
        registro_dispositivos_estricto=settings.device_registry_estricto,
        audit_log_repository=SQLAlchemyAuditLogRepository(session),
        ventana_normalizacion_alerta_minutos=settings.alerta_ventana_normalizacion_minutos,
        minutos_normales_preventiva=settings.alerta_preventiva_minutos_normales,
        antiguedad_maxima_horas=settings.lectura_antiguedad_max_horas,
        ia_legado_modo=modo_legado,
        ia_timeout_s=settings.ia_inferencia_timeout_s,
        ia_prueba_retardo_s=settings.ia_prueba_retardo_s,
        ia_prueba_excepcion=settings.ia_prueba_excepcion,
    )
    lectura = LecturaTermica(
        device_id=body.device_id,
        timestamp=body.timestamp,
        temperatura_ambiental=body.temperatura_ambiental,
        humedad_ambiental=body.humedad_ambiental,
        temperatura_interna=body.temperatura_interna,
        apertura_refrigerador=body.apertura_refrigerador,
        estado_conectividad=body.estado_conectividad,
        reading_id=f"manual-{uuid4().hex}",
        origen=ORIGEN_MANUAL,
        registrado_por=usuario.id,
        payload={"duracion_apertura_segundos": body.duracion_apertura_segundos},
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
    # La bitácora se encadena en la cadena de SISTEMA: el eslabón de la propia
    # lectura (cadena del dispositivo) ya declara origen y autor, y duplicarlo
    # allí alteraría la secuencia de eventos de la unidad monitoreada.
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), trazabilidad_repository
    ).execute(
        usuario_id=usuario.id,
        accion="LECTURA_MANUAL_REGISTRADA",
        recurso=f"lecturas/{lectura_guardada.id}",
        detalle={
            "device_id": body.device_id,
            "timestamp": body.timestamp.isoformat(),
            "reading_id": lectura_guardada.reading_id,
        },
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()

    # HU-53 (D2): mismo despachador único que la ingesta MQTT, tras el COMMIT.
    despachador = getattr(request.app.state, "notificaciones", None)
    if despachador is not None:
        despachador.encolar_todos(use_case.avisos_pendientes)

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
    await broadcaster.publicar(evento, tipo_sse_lectura(lectura_guardada))

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
                "normalizacion_desde": (
                    episodio_actual.normalizacion_desde.isoformat()
                    if episodio_actual.normalizacion_desde else None
                ),
            },
            "alerta" if episodio_previo is None else "episodio_actualizado",
        )


# Cabecera con el total sin paginar de GET /api/lecturas (expuesta por CORS).
CABECERA_TOTAL = "X-Total-Count"


@router.get("", response_model=list[LecturaResponse])
async def listar_historial(
    session: DbSessionDep,
    response: Response,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
    device_id: str | None = Query(default=None, pattern=PATRON_DEVICE_ID),
    nivel_riesgo: str | None = Query(default=None, description="Clase cruda del modelo (model_class)"),
    estado_conectividad: str | None = None,
    # HU-36 / B-14: el filtro que usa el historial; coincide con lo que la
    # tabla muestra (riesgo efectivo), no con la clase cruda del modelo.
    riesgo_efectivo: NivelRiesgo | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    # 5000 = MAX_LECTURAS_EN_MEMORIA del frontend (HU-31): la ventana de 24 h
    # por defecto a 30 s/lectura son 2880 filas; con 1000 la API respondía 422.
    limite: int = Query(default=100, ge=1, le=5000),
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
    filtros = {
        "device_id": device_id,
        "nivel_riesgo": nivel_riesgo,
        "estado_conectividad": estado_conectividad,
        "riesgo_efectivo": riesgo_efectivo.value if riesgo_efectivo else None,
        "desde": desde,
        "hasta": hasta,
    }
    lecturas = await use_case.execute(**filtros, limite=limite, offset=offset)
    # HU-31/HU-36: total sin `limite`/`offset`, para paginar la tabla y avisar
    # cuando lo mostrado no cubre todo el periodo. Si la página no está llena
    # con al menos una fila (o empieza en 0), el total ya se conoce sin contar.
    if len(lecturas) < limite and (offset == 0 or lecturas):
        total = offset + len(lecturas)
    else:
        total = await lectura_repository.contar(**filtros)
    response.headers[CABECERA_TOTAL] = str(total)
    return [lectura_to_response(lectura) for lectura in lecturas]


@router.get("/serie", response_model=SerieTermicaResponse)
async def serie_termica(
    session: DbSessionDep,
    settings: SettingsDep,
    _usuario=Depends(require_roles(Rol.TECNICO, Rol.FARMACEUTICO, Rol.AUDITOR)),
    device_id: str = Query(pattern=PATRON_DEVICE_ID),
    desde: datetime | None = Query(default=None, description="Por defecto, 24 h antes de `hasta` (HU-31 esc. 4)"),
    hasta: datetime | None = Query(default=None, description="Por defecto, ahora"),
    bucket: str = Query(
        default="auto",
        pattern=r"^(auto|\d{1,6})$",
        description="Segundos por intervalo (10 a 86400) o `auto` (a lo sumo 1500 intervalos)",
    ),
) -> SerieTermicaResponse:
    """HU-31 / HU-36: serie agregada (mín., máx., promedio y conteo por
    intervalo) del periodo COMPLETO, con brechas y resumen. Reemplaza, para la
    curva y el resumen, a pedir miles de lecturas crudas con tope. Mismos
    roles que GET /api/lecturas. Se declara antes de `/{lectura_id}`."""
    device = await SQLAlchemyDeviceRepository(session).obtener(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dispositivo no encontrado")
    try:
        serie = await ConsultarSerieTermicaUseCase(
            SQLAlchemyLecturaRepository(session), SQLAlchemyEstadoNodoRepository(session)
        ).execute(
            device_id,
            desde,
            hasta,
            intervalo_reporte_segundos=device.get("intervalo_reporte_segundos") or 30,
            n_intervalos_sin_datos=settings.sin_datos_n_intervalos,
            bucket_segundos=None if bucket == "auto" else int(bucket),
            registrado_en=device.get("created_at"),
        )
    except RangoSerieInvalidoError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return serie_to_response(serie)


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
