from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.gestionar_modelo_ia import (
    ActivacionBloqueadaError,
    ActivarVersionModeloUseCase,
    ConsultarInferenciaLecturaUseCase,
    DesactivarVersionModeloUseCase,
    RegistrarVersionModeloUseCase,
    VersionNoActivaError,
    evaluar_criterios_modelo,
    motivos_bloqueo_activacion,
)
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.value_objects.rol import Rol
from src.infrastructure.ai import gobierno
from src.infrastructure.ai.features import FeaturesRiesgoTermico
from src.infrastructure.ai.prediccion_preventiva import registro as registro_preventivo
from src.infrastructure.ai.prediccion_preventiva.servicio import obtener_servicio_preventivo
from src.infrastructure.ai.random_forest_service import (
    CODIGOS_RECHAZO,
    get_random_forest_service,
)
from src.infrastructure.config import get_settings
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository
from src.infrastructure.database.repositories.modelo_ia_repository import SQLAlchemyModeloIARepository
from src.infrastructure.database.repositories.prediccion_preventiva_repository import (
    SQLAlchemyPrediccionPreventivaRepository,
)
from src.infrastructure.database.repositories.trazabilidad_repository import (
    SQLAlchemyTrazabilidadRepository,
)
from src.interface.api.deps import CurrentUserDep, DbSessionDep, SettingsDep, require_roles
from src.interface.api.schemas import (
    DesactivarVersionModeloRequest,
    InferenciaLecturaResponse,
    RegistrarVersionModeloRequest,
    VersionModeloResponse,
)

router = APIRouter(prefix="/api/ia", tags=["ia"])


def _version_a_response(v: VersionModeloIA) -> VersionModeloResponse:
    criterio = get_settings().ia_criterio_principal
    motivos = motivos_bloqueo_activacion(v, criterio)
    return VersionModeloResponse(
        horizonte_min=v.horizonte_min,
        metricas=v.metricas,
        particiones=v.particiones,
        evaluacion_criterios=evaluar_criterios_modelo(v.metricas),
        criterio_principal=criterio,
        activable=not motivos,
        motivos_bloqueo=motivos,
        id=v.id,
        version=v.version,
        model_hash=v.model_hash,
        feature_schema_version=v.feature_schema_version,
        dataset_version=v.dataset_version,
        scikit_learn_version=v.scikit_learn_version,
        python_version=v.python_version,
        trained_at=v.trained_at,
        aprobado_por=v.aprobado_por,
        aprobado_en=v.aprobado_en,
        activa=v.activa,
        activada_en=v.activada_en,
        desactivada_en=v.desactivada_en,
    )


class ClasificacionRequest(BaseModel):
    """Vector de features para una clasificación de prueba (validación técnica)."""

    temperatura_ambiental: float = Field(ge=-40.0, le=125.0)
    humedad_ambiental: float = Field(ge=0.0, le=100.0)
    temperatura_interna: float = Field(ge=-55.0, le=125.0)
    diferencia_sensores: float
    duracion_fuera_rango: float = Field(ge=0.0, le=1440.0)
    frecuencia_desviaciones: float = Field(ge=0.0)
    tendencia_termica: float
    apertura_refrigerador: bool = False
    hora_evento: int = Field(ge=0, le=23)
    estado_conectividad_online: bool = True


class ClasificacionResponse(BaseModel):
    # BE-024: sin modelo cargado (o si la inferencia falla) decide la regla de
    # salvaguarda y no hay probabilidad de Random Forest: `confianza` es null,
    # nunca un 500. `estado_inferencia` explica por qué.
    nivel_riesgo: str | None
    confianza: float | None
    origen: str
    estado_inferencia: str


def _no_disponible(detalle: str, codigo: str, rechazado: bool) -> JSONResponse:
    """503 del modelo legado: `detail` como siempre y, aditivos, `motivo`
    (no_entrenado, sin_metricas, checksum_invalido, hash_distinto_de_la_version_activa,
    sin_verificacion_de_integridad, incompatible, error_de_lectura) y
    `rechazado_por_verificacion`."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": detalle, "motivo": codigo, "rechazado_por_verificacion": rechazado},
    )


@router.get(
    "/modelo",
    responses={503: {"description": "Modelo no disponible: `motivo` distingue no entrenado de rechazado."}},
)
async def obtener_metadata_modelo(
    _usuario=Depends(require_roles(Rol.FARMACEUTICO, Rol.AUDITOR)),
) -> dict:
    """Evidencia del modelo Random Forest para la validación técnica (RNF-04):
    metadatos del artefacto y métricas de entrenamiento (accuracy, precision,
    recall, F1 por clase, matriz de confusión y validación cruzada)."""
    servicio = get_random_forest_service()
    metricas = servicio.metricas_entrenamiento()
    # HU-16 esc. 2: un artefacto alterado o que no es el de la versión activa
    # deja el modelo «no disponible» (503), nunca un 500. P1-7: el cuerpo dice
    # además, con un código estable, si fue un rechazo por verificación o si
    # simplemente no hay modelo entrenado (`detail` se conserva igual).
    codigo = servicio.motivo_codigo()
    if codigo in CODIGOS_RECHAZO:
        return _no_disponible(
            "El modelo Random Forest no está disponible: el artefacto no superó la verificación de "
            "integridad o compatibilidad al cargarse.",
            codigo,
            rechazado=True,
        )
    if codigo is not None or metricas is None:
        return _no_disponible(
            "El modelo Random Forest no está entrenado en este entorno.",
            codigo or "sin_metricas",
            rechazado=False,
        )
    return {
        "modelo_disponible": True,
        "metadata": servicio.metadata,
        "metricas": metricas,
        # El modelo legado se entrenó con escenarios SINTÉTICOS etiquetados
        # por la misma regla que luego imita (train_model_v3.py): sus métricas
        # no son evidencia de capacidad preventiva. El frontend ya muestra
        # "no verificado" salvo que datos_simulados sea false
        # (docs/ml/01_DATASET_AUDIT.md, sección "Modelo existente").
        "datos_simulados": True,
        "estado_evaluacion": "ilustrativa_no_reportable",
    }


@router.get("/preventivo")
async def obtener_estado_modelo_preventivo(
    settings: SettingsDep,
    _usuario=Depends(require_roles(Rol.FARMACEUTICO, Rol.AUDITOR, Rol.ADMINISTRADOR)),
) -> dict:
    """Estado del modelo preventivo temporal: modo configurado, versión
    cargada, su estado de aprobación, métricas offline y criterios. Solo
    lectura: entrenar, subir o activar modelos no se expone por HTTP."""
    servicio = obtener_servicio_preventivo(
        settings.ia_preventivo_modo, settings.ia_preventivo_version, settings.ia_preventivo_sha256
    )
    return {**servicio.estado(), "versiones_registradas": registro_preventivo.listar()}


@router.get("/preventivo/predicciones")
async def listar_predicciones_preventivas(
    session: DbSessionDep,
    device_id: str | None = Query(default=None, max_length=50),
    limite: int = Query(default=100, ge=1, le=1000),
    _usuario=Depends(require_roles(Rol.FARMACEUTICO, Rol.AUDITOR)),
) -> list[dict]:
    """Predicciones registradas (modo sombra o activo), separadas de las
    mediciones. Evidencia prospectiva para el Experimento D."""
    predicciones = await SQLAlchemyPrediccionPreventivaRepository(session).listar(device_id=device_id, limite=limite)
    return [
        {
            "id": p.id,
            "lectura_id": p.lectura_id,
            "device_id": p.device_id,
            "timestamp_inferencia": p.timestamp_inferencia,
            "modo": p.modo,
            "estado": p.estado,
            "clase": p.clase.value if p.clase is not None else None,
            "puntuacion": p.puntuacion,
            "puntuacion_calibrada": p.puntuacion_calibrada,
            "umbral": p.umbral,
            "horizonte_min": p.horizonte_min,
            "modelo_version": p.modelo_version,
            "motivo": p.motivo,
        }
        for p in predicciones
    ]


@router.post("/clasificar", response_model=ClasificacionResponse)
async def clasificar_prueba(
    body: ClasificacionRequest,
    _usuario=Depends(require_roles(Rol.FARMACEUTICO)),
) -> ClasificacionResponse:
    """Clasificación bajo demanda para las pruebas E2E de la tesis (RF-08):
    permite verificar la respuesta del clasificador ante un vector arbitrario
    sin registrar lecturas ni generar alertas."""
    servicio = get_random_forest_service()
    resultado = servicio.inferir(FeaturesRiesgoTermico(**body.model_dump()))
    return ClasificacionResponse(
        nivel_riesgo=resultado.nivel.value if resultado.nivel is not None else None,
        confianza=round(resultado.confianza, 4) if resultado.confianza is not None else None,
        origen=resultado.origen,
        estado_inferencia=resultado.estado_inferencia,
    )


@router.post(
    "/modelo/versiones",
    response_model=VersionModeloResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {
            "description": "La versión ya está registrada o, con `activar=true`, no puede activarse "
            "(nada queda registrado). En el segundo caso, `codigos` trae un código estable por "
            "motivo, igual que POST /modelo/versiones/{version}/activar.",
        },
    },
)
async def registrar_version_modelo(
    body: RegistrarVersionModeloRequest,
    session: DbSessionDep,
    request: Request,
    settings: SettingsDep,
    admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> VersionModeloResponse:
    """HU-46 Escenario 1: registra una versión aprobada del modelo. Con
    `activar=true` además la marca como versión activa de producción, con las
    mismas validaciones y auditoría que POST /modelo/versiones/{version}/activar;
    si la activación se rechaza (409), no queda ni registrada ni activa y el
    rechazo queda en la bitácora. Sin `activar` solo la registra."""
    repositorio = SQLAlchemyModeloIARepository(session)
    use_case = RegistrarVersionModeloUseCase(
        repositorio, RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session))
    )
    if body.activar:
        # Mismo orden de candados que /activar y /desactivar: primero el del
        # cambio de versión y después el de la cadena SISTEMA, que el registro
        # toma ya. Al revés, un /desactivar simultáneo era un interbloqueo en
        # PostgreSQL (500 para uno de los dos). El candado es reentrante: la
        # activación de más abajo lo vuelve a pedir sin esperar.
        await repositorio.bloquear_cambio_de_version()
    try:
        registrada = await use_case.execute(
            version=body.version,
            model_hash=body.model_hash,
            feature_schema_version=body.feature_schema_version,
            dataset_version=body.dataset_version,
            scikit_learn_version=body.scikit_learn_version,
            python_version=body.python_version,
            trained_at=body.trained_at,
            admin_id=admin.id,
            horizonte_min=body.horizonte_min,
            metricas=body.metricas,
            particiones=body.particiones,
        )
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="REGISTRAR_VERSION_MODELO_IA",
        recurso=f"ia/modelo/versiones/{body.version}",
        detalle={"version": body.version, "model_hash": body.model_hash},
        ip_origen=request.client.host if request.client else None,
    )
    if not body.activar:
        await session.commit()
        return _version_a_response(registrada)

    ip_origen = request.client.host if request.client else None
    try:
        activada = await _ejecutar_activacion(session, settings, body.version, admin.id)
    except ActivacionBloqueadaError as exc:
        # Registrar y activar es una sola operación: nada queda a medias.
        await session.rollback()
        await _auditar_rechazo_activacion(
            session, admin.id, body.version, ip_origen,
            {
                "motivo": "criterios_no_cumplidos", "motivos": exc.motivos, "codigos": exc.codigos,
                "al_registrar": True,
            },
        )
        await session.commit()
        # Mismas validaciones que /activar (IA-B05 v6 y v7 incluidas) y mismo
        # cuerpo del 409: `detail` en texto y `codigos` estables (aditivo).
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": f"La versión no se registró ni se activó: {exc}", "codigos": exc.codigos},
        )
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="ACTIVAR_VERSION_MODELO_IA",
        recurso=f"ia/modelo/versiones/{body.version}/activar",
        detalle={"version": body.version, "al_registrar": True},
        ip_origen=ip_origen,
    )
    await session.commit()
    # HU-46 esc. 3: desde aquí solo se carga el artefacto de la versión activa.
    gobierno.aplicar_version_activa(settings, activada)
    return _version_a_response(activada)


async def _ejecutar_activacion(session, settings, version: str, admin_id):
    """HU-16 esc. 4 / HU-46: se valida el artefacto que realmente se cargará
    (el preventivo con IA_PREVENTIVO_MODO=activo; si no, el legado)."""
    use_case = ActivarVersionModeloUseCase(
        SQLAlchemyModeloIARepository(session),
        RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)),
        settings.ia_criterio_principal,
        verificar_artefacto=gobierno.verificador_de_activacion(settings),
        modelo=gobierno.modelo_gobernado(settings),
    )
    return await use_case.execute(version, admin_id)


async def _auditar_rechazo_activacion(session, admin_id, version: str, ip_origen, detalle: dict) -> None:
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin_id,
        accion="ACTIVAR_VERSION_MODELO_IA_RECHAZADO",
        recurso=f"ia/modelo/versiones/{version}/activar",
        detalle=detalle,
        ip_origen=ip_origen,
    )


@router.get("/modelo/versiones", response_model=list[VersionModeloResponse])
async def listar_versiones_modelo(
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.ADMINISTRADOR, Rol.AUDITOR)),
) -> list[VersionModeloResponse]:
    repositorio = SQLAlchemyModeloIARepository(session)
    return [_version_a_response(v) for v in await repositorio.listar()]


@router.post(
    "/modelo/versiones/{version}/activar",
    response_model=VersionModeloResponse,
    responses={
        404: {"description": "Versión no registrada."},
        409: {
            "description": "La versión no puede activarse. `detail` trae los motivos en texto y "
            "`codigos`, un código estable por motivo y en el mismo orden.",
        },
    },
)
async def activar_version_modelo(
    version: str,
    session: DbSessionDep,
    request: Request,
    settings: SettingsDep,
    admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> VersionModeloResponse:
    """HU-46 Escenario 2: activar una versión no registrada se rechaza (404)
    — la versión activa vigente permanece intacta. Escenario 4 de HU-18: una
    versión sin métricas, H y particiones, o que no cumple el criterio
    principal, tampoco se activa (409)."""
    ip_origen = request.client.host if request.client else None
    try:
        activada = await _ejecutar_activacion(session, settings, version, admin.id)
    except ActivacionBloqueadaError as exc:
        await _auditar_rechazo_activacion(
            session, admin.id, version, ip_origen,
            {"motivo": "criterios_no_cumplidos", "motivos": exc.motivos, "codigos": exc.codigos},
        )
        await session.commit()
        # `detail` conserva el texto de siempre; `codigos` es aditivo (IA-B05).
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc), "codigos": exc.codigos}
        )
    except RecursoNoEncontradoError as exc:
        await _auditar_rechazo_activacion(session, admin.id, version, ip_origen, {"motivo": "version_no_registrada"})
        await session.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=admin.id,
        accion="ACTIVAR_VERSION_MODELO_IA",
        recurso=f"ia/modelo/versiones/{version}/activar",
        detalle={"version": version},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    # HU-46 esc. 3: desde aquí solo se carga el artefacto de la versión activa.
    gobierno.aplicar_version_activa(settings, activada)
    return _version_a_response(activada)


@router.post(
    "/modelo/versiones/{version}/desactivar",
    response_model=VersionModeloResponse,
    responses={
        404: {"description": "Versión no registrada."},
        409: {"description": "`detail` = `version_no_activa`: la versión no es la activa (o ya se desactivó)."},
    },
)
async def desactivar_version_modelo(
    version: str,
    body: DesactivarVersionModeloRequest,
    session: DbSessionDep,
    request: Request,
    settings: SettingsDep,
    admin=Depends(require_roles(Rol.ADMINISTRADOR)),
) -> VersionModeloResponse:
    """P-12: desactiva la versión activa sin activar otra. No queda ninguna
    versión activa: el modelo preventivo en modo activo deja de cargarse
    (`modelo_ia.preventivo.motivo_codigo=sin_version_activa_registrada` en
    /health/detalle) y el riesgo efectivo vuelve a la línea base, la misma de
    antes de cualquier activación. Queda el evento encadenado
    MODELO_IA_VERSION_DESACTIVADA y la fila DESACTIVAR_VERSION_MODELO_IA."""
    repositorio = SQLAlchemyModeloIARepository(session)
    use_case = DesactivarVersionModeloUseCase(
        repositorio, RegistrarHashEncadenadoUseCase(SQLAlchemyTrazabilidadRepository(session)),
        modelo=gobierno.modelo_gobernado(settings),
    )
    auditoria = AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    )
    recurso = f"ia/modelo/versiones/{version}/desactivar"[:100]
    ip_origen = request.client.host if request.client else None
    try:
        desactivada = await use_case.execute(version, admin.id, body.motivo)
    except (RecursoNoEncontradoError, VersionNoActivaError) as exc:
        no_activa = isinstance(exc, VersionNoActivaError)
        await auditoria.execute(
            usuario_id=admin.id,
            accion="DESACTIVAR_VERSION_MODELO_IA_RECHAZADO",
            recurso=recurso,
            detalle={
                "motivo": VersionNoActivaError.codigo if no_activa else "version_no_registrada",
                "justificacion": body.motivo,
            },
            ip_origen=ip_origen,
        )
        await session.commit()
        if no_activa:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=VersionNoActivaError.codigo) from exc
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    await auditoria.execute(
        usuario_id=admin.id,
        accion="DESACTIVAR_VERSION_MODELO_IA",
        recurso=recurso,
        detalle={"version": version, "motivo": body.motivo, "model_hash": desactivada.model_hash},
        ip_origen=ip_origen,
    )
    await session.commit()
    # Sin versión activa: el preventivo no carga y el legado vuelve a su
    # verificación de siempre (línea base, P-12).
    gobierno.aplicar_version_activa(settings, None)
    return _version_a_response(desactivada)


@router.get("/inferencia/{lectura_id}", response_model=InferenciaLecturaResponse)
async def consultar_inferencia_lectura(
    lectura_id: UUID,
    session: DbSessionDep,
    _usuario=Depends(require_roles(Rol.FARMACEUTICO, Rol.AUDITOR)),
) -> InferenciaLecturaResponse:
    """HU-47: qué versión del modelo, qué vector de features y qué
    probabilidad por clase produjeron la clasificación de una lectura
    concreta. Vista de solo lectura — no existe endpoint de escritura."""
    use_case = ConsultarInferenciaLecturaUseCase(
        SQLAlchemyLecturaRepository(session), SQLAlchemyModeloIARepository(session)
    )
    try:
        resultado = await use_case.execute(lectura_id)
    except RecursoNoEncontradoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if resultado["feature_schema_version"] is None and resultado["modelo_version"] is not None:
        metadata = get_random_forest_service().metadata or {}
        resultado["feature_schema_version"] = metadata.get("feature_schema_version")
    return InferenciaLecturaResponse(**resultado)


@router.api_route(
    # HU-47 esc. 3: cualquier método de escritura, incluido POST.
    "/inferencia/{lectura_id}", methods=["POST", "PUT", "PATCH", "DELETE"], include_in_schema=False
)
async def rechazar_modificacion_inferencia(
    lectura_id: UUID,
    session: DbSessionDep,
    request: Request,
    usuario: CurrentUserDep,
) -> None:
    """HU-47 esc. 3: la evidencia es de solo lectura y el intento queda auditado."""
    await AuditarAccionCriticaUseCase(
        SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
    ).execute(
        usuario_id=usuario.id,
        accion="INTENTO_MODIFICAR_INFERENCIA",
        recurso=f"ia/inferencia/{lectura_id}",
        detalle={"metodo": request.method},
        ip_origen=request.client.host if request.client else None,
    )
    await session.commit()
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="La evidencia de inferencia es de solo lectura.",
        headers={"Allow": "GET"},
    )
