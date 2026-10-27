from datetime import datetime, timezone
from uuid import UUID

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.repositories.i_modelo_ia_repository import IModeloIARepository

# HU-46 / HU-18 Escenario 4: los dos juegos de criterios que figuran en la
# documentación. El principal lo fija IA_CRITERIO_PRINCIPAL (decisión con el
# asesor); el otro se calcula y se reporta igual. Los umbrales por incidente
# son los de prediccion_preventiva.experimentos.CRITERIOS_ACEPTACION.
CRITERIO_F1 = "f1"
CRITERIO_POR_INCIDENTE = "por_incidente"
UMBRAL_F1_MACRO = 0.85
UMBRALES_POR_INCIDENTE = {
    "incidentes_evaluables": ("min", 10),
    "sensibilidad_incidente": ("min", 0.80),
    "ic95_inferior_sensibilidad": ("min", 0.50),
    "falsas_alarmas_por_dia_sensor": ("max", 2.0),
    "mediana_anticipacion_min": ("min", 5.0),
}
CAMPOS_PARTICIONES = ("entrenamiento", "prueba", "separacion_min")


def _numero(valor) -> float | None:
    return float(valor) if isinstance(valor, int | float) and not isinstance(valor, bool) else None


def evaluar_criterios_modelo(metricas: dict | None) -> dict:
    """Evalúa una versión con ambos juegos de criterios. `cumple` es None
    cuando faltan las métricas necesarias para decidir."""
    metricas = metricas or {}
    f1 = _numero(metricas.get("f1_macro"))
    detalle_incidente = {}
    for nombre, (sentido, umbral) in UMBRALES_POR_INCIDENTE.items():
        valor = _numero(metricas.get(nombre))
        cumple = None if valor is None else (valor >= umbral if sentido == "min" else valor <= umbral)
        detalle_incidente[nombre] = {"valor": valor, "umbral": umbral, "cumple": cumple}
    rf_ap, base_ap = _numero(metricas.get("ap_random_forest")), _numero(metricas.get("ap_mejor_baseline"))
    detalle_incidente["supera_mejor_baseline_ap"] = {
        "valor": rf_ap, "umbral": base_ap,
        "cumple": None if rf_ap is None or base_ap is None else rf_ap > base_ap,
    }
    estados = [d["cumple"] for d in detalle_incidente.values()]
    return {
        CRITERIO_F1: {"valor": f1, "umbral": UMBRAL_F1_MACRO, "cumple": None if f1 is None else f1 >= UMBRAL_F1_MACRO},
        CRITERIO_POR_INCIDENTE: {
            "detalle": detalle_incidente,
            "cumple": None if None in estados else all(estados),
        },
    }


def motivos_bloqueo_activacion(version: VersionModeloIA, criterio_principal: str) -> list[str]:
    """HU-46: qué impide activar la versión. Lista vacía = puede activarse."""
    motivos = []
    if not version.horizonte_min or version.horizonte_min <= 0:
        motivos.append("falta el horizonte H de la etiqueta")
    if not version.feature_schema_version:
        motivos.append("falta la versión del esquema de features")
    particiones = version.particiones or {}
    faltan = [c for c in CAMPOS_PARTICIONES if particiones.get(c) in (None, "", [])]
    if faltan:
        motivos.append(f"faltan las particiones ({', '.join(faltan)})")
    elif _numero(particiones.get("separacion_min")) is not None and version.horizonte_min and (
        particiones["separacion_min"] < version.horizonte_min
    ):
        motivos.append("la separación entre particiones es menor que H")
    evaluacion = evaluar_criterios_modelo(version.metricas)
    principal = evaluacion.get(criterio_principal)
    if principal is None:
        motivos.append(f"criterio principal desconocido: {criterio_principal}")
    elif principal["cumple"] is None:
        motivos.append(f"faltan métricas del criterio principal ({criterio_principal})")
    elif not principal["cumple"]:
        motivos.append(f"no cumple el criterio principal ({criterio_principal})")
    return motivos


class ActivacionBloqueadaError(DomainError):
    def __init__(self, version: str, motivos: list[str]) -> None:
        super().__init__(f"La versión {version} no puede activarse: " + "; ".join(motivos))
        self.motivos = motivos


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
        horizonte_min: int | None = None,
        metricas: dict | None = None,
        particiones: dict | None = None,
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
                horizonte_min=horizonte_min,
                metricas=metricas,
                particiones=particiones,
            )
        )

        await self._registrar_hash.execute(
            tipo_evento="MODELO_IA_VERSION_REGISTRADA",
            payload={
                "version": version,
                "model_hash": model_hash,
                "feature_schema_version": feature_schema_version,
                "dataset_version": dataset_version,
                "horizonte_min": horizonte_min,
                "metricas": metricas,
                "particiones": particiones,
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
        self,
        modelo_repository: IModeloIARepository,
        registrar_hash: RegistrarHashEncadenadoUseCase,
        criterio_principal: str = CRITERIO_POR_INCIDENTE,
    ) -> None:
        self._modelo_repository = modelo_repository
        self._registrar_hash = registrar_hash
        self._criterio = criterio_principal

    async def execute(self, version: str, admin_id: UUID) -> VersionModeloIA:
        objetivo = await self._modelo_repository.obtener_por_version(version)
        if objetivo is None:
            raise RecursoNoEncontradoError(
                f"La versión {version} no está registrada/aprobada; no puede activarse"
            )
        # HU-46 / HU-18 Escenario 4: sin métricas, H y particiones, o sin
        # cumplir el criterio principal, la versión no entra en producción.
        motivos = motivos_bloqueo_activacion(objetivo, self._criterio)
        if motivos:
            raise ActivacionBloqueadaError(version, motivos)

        anterior = await self._modelo_repository.obtener_activa()
        cuando = datetime.now(tz=timezone.utc)
        activada = await self._modelo_repository.activar(version, cuando)

        await self._registrar_hash.execute(
            tipo_evento="MODELO_IA_VERSION_ACTIVADA",
            payload={
                "version_anterior": anterior.version if anterior is not None else None,
                "version_nueva": version,
                "admin_id": str(admin_id),
                "criterio_principal": self._criterio,
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
