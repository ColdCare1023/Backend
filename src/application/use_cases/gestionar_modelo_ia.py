from datetime import datetime, timezone
from uuid import UUID

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.repositories.i_modelo_ia_repository import IModeloIARepository


class RegistrarVersionModeloUseCase:
    """HU-46 Escenario 1: registra una nueva versión aprobada del modelo con
    su hash SHA-256 y metadatos de reproducibilidad. Registrar NO activa por
    sí solo — activar es una operación explícita separada (ActivarVersionModeloUseCase),
    así se puede tener más de una versión aprobada sin que la última
    registrada reemplace en automático a la que está en producción."""

    def __init__(
        self, modelo_repository: IModeloIARepository, registrar_hash: RegistrarHashEncadenadoUseCase
    ) -> None:
        self._modelo_repository = modelo_repository
        self._registrar_hash = registrar_hash

    async def execute(
        self,
        version: str,
        model_hash: str,
        feature_schema_version: str,
        dataset_version: str,
        scikit_learn_version: str,
        python_version: str,
        trained_at: datetime,
        admin_id: UUID,
    ) -> VersionModeloIA:
        existente = await self._modelo_repository.obtener_por_version(version)
        if existente is not None:
            raise DomainError(f"La versión {version} ya está registrada")

        registrada = await self._modelo_repository.registrar(
            VersionModeloIA(
                version=version,
                model_hash=model_hash,
                feature_schema_version=feature_schema_version,
                dataset_version=dataset_version,
                scikit_learn_version=scikit_learn_version,
                python_version=python_version,
                trained_at=trained_at,
                aprobado_por=admin_id,
            )
        )

        await self._registrar_hash.execute(
            tipo_evento="MODELO_IA_VERSION_REGISTRADA",
            payload={
                "version": version,
                "model_hash": model_hash,
                "feature_schema_version": feature_schema_version,
                "dataset_version": dataset_version,
            },
            usuario_id=admin_id,
        )
        return registrada


class ActivarVersionModeloUseCase:
    """HU-46 Escenario 2: activar una versión NO registrada se rechaza — la
    versión activa vigente se mantiene, y el intento queda auditado por el
    llamador (ver ia_router.py) además del evento de trazabilidad que este
    caso de uso genera cuando SÍ tiene éxito."""

    def __init__(
        self, modelo_repository: IModeloIARepository, registrar_hash: RegistrarHashEncadenadoUseCase
    ) -> None:
        self._modelo_repository = modelo_repository
        self._registrar_hash = registrar_hash

    async def execute(self, version: str, admin_id: UUID) -> VersionModeloIA:
        objetivo = await self._modelo_repository.obtener_por_version(version)
        if objetivo is None:
            raise RecursoNoEncontradoError(
                f"La versión {version} no está registrada/aprobada; no puede activarse"
            )

        anterior = await self._modelo_repository.obtener_activa()
        cuando = datetime.now(tz=timezone.utc)
        activada = await self._modelo_repository.activar(version, cuando)

        await self._registrar_hash.execute(
            tipo_evento="MODELO_IA_VERSION_ACTIVADA",
            payload={
                "version_anterior": anterior.version if anterior is not None else None,
                "version_nueva": version,
                "admin_id": str(admin_id),
            },
            usuario_id=admin_id,
            timestamp=cuando,
        )
        return activada


class ConsultarInferenciaLecturaUseCase:
    """HU-47: consulta de solo lectura de la evidencia completa de la
    inferencia asociada a una lectura térmica concreta."""

    def __init__(
        self, lectura_repository: ILecturaRepository, modelo_repository: IModeloIARepository
    ) -> None:
        self._lectura_repository = lectura_repository
        self._modelo_repository = modelo_repository

    async def execute(self, lectura_id: UUID) -> dict:
        lectura = await self._lectura_repository.obtener_por_id(lectura_id)
        if lectura is None:
            raise RecursoNoEncontradoError(f"Lectura {lectura_id} no encontrada")

        version_vigente_en_ese_momento = None
        coincide_con_version_activa_historica = None
        if lectura.modelo_version is not None:
            version_vigente_en_ese_momento = await self._modelo_repository.obtener_version_activa_en(
                lectura.timestamp
            )
            coincide_con_version_activa_historica = (
                version_vigente_en_ese_momento is not None
                and version_vigente_en_ese_momento.version == lectura.modelo_version
            )

        return {
            "lectura_id": lectura.id,
            "device_id": lectura.device_id,
            "timestamp_inferencia": lectura.timestamp,
            "modelo_version": lectura.modelo_version,
            "vector_features": lectura.vector_features_ia,
            "probabilidades_por_clase": lectura.probabilidades_ia,
            "clasificacion_final": lectura.nivel_riesgo,
            "confianza": lectura.confianza_ia,
            "origen_clasificacion": lectura.origen_clasificacion,
            "version_era_activa_y_aprobada_en_ese_momento": coincide_con_version_activa_historica,
        }
