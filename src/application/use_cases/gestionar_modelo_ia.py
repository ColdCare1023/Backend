from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.version_modelo_ia import VersionModeloIA
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.repositories.i_modelo_ia_repository import IModeloIARepository
from src.domain.value_objects.motivo_bloqueo import MotivoBloqueo, codigo_de
from src.infrastructure.ai.random_forest_service import ESTADO_COMPLETADA

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

# Códigos estables de los motivos de bloqueo por criterios (409 de /activar,
# campo `codigos`; el texto sigue en `detail` y en `motivos_bloqueo`).
BLOQUEO_FALTA_HORIZONTE = "falta_horizonte"
BLOQUEO_FALTA_ESQUEMA = "falta_esquema_features"
BLOQUEO_FALTAN_PARTICIONES = "faltan_particiones"
BLOQUEO_SEPARACION_MENOR_QUE_H = "separacion_menor_que_horizonte"
BLOQUEO_CRITERIO_DESCONOCIDO = "criterio_principal_desconocido"
BLOQUEO_FALTAN_METRICAS = "faltan_metricas_criterio_principal"
BLOQUEO_NO_CUMPLE_CRITERIO = "no_cumple_criterio_principal"


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


def motivos_bloqueo_activacion(version: VersionModeloIA, criterio_principal: str) -> list[MotivoBloqueo]:
    """HU-46: qué impide activar la versión. Lista vacía = puede activarse.
    Cada motivo es su texto (str) y lleva además un código estable."""
    motivos = []
    if not version.horizonte_min or version.horizonte_min <= 0:
        motivos.append(MotivoBloqueo(BLOQUEO_FALTA_HORIZONTE, "falta el horizonte H de la etiqueta"))
    if not version.feature_schema_version:
        motivos.append(MotivoBloqueo(BLOQUEO_FALTA_ESQUEMA, "falta la versión del esquema de features"))
    particiones = version.particiones or {}
    faltan = [c for c in CAMPOS_PARTICIONES if particiones.get(c) in (None, "", [])]
    if faltan:
        motivos.append(MotivoBloqueo(BLOQUEO_FALTAN_PARTICIONES, f"faltan las particiones ({', '.join(faltan)})"))
    elif _numero(particiones.get("separacion_min")) is not None and version.horizonte_min and (
        particiones["separacion_min"] < version.horizonte_min
    ):
        motivos.append(MotivoBloqueo(BLOQUEO_SEPARACION_MENOR_QUE_H, "la separación entre particiones es menor que H"))
    evaluacion = evaluar_criterios_modelo(version.metricas)
    principal = evaluacion.get(criterio_principal)
    if principal is None:
        motivos.append(MotivoBloqueo(BLOQUEO_CRITERIO_DESCONOCIDO, f"criterio principal desconocido: {criterio_principal}"))
    elif principal["cumple"] is None:
        motivos.append(MotivoBloqueo(
            BLOQUEO_FALTAN_METRICAS, f"faltan métricas del criterio principal ({criterio_principal})"
        ))
    elif not principal["cumple"]:
        motivos.append(MotivoBloqueo(BLOQUEO_NO_CUMPLE_CRITERIO, f"no cumple el criterio principal ({criterio_principal})"))
    return motivos


class ActivacionBloqueadaError(DomainError):
    def __init__(self, version: str, motivos: list[str]) -> None:
        super().__init__(f"La versión {version} no puede activarse: " + "; ".join(motivos))
        self.motivos = motivos
        # Un código estable por motivo, en el mismo orden (409 de /activar).
        self.codigos = [codigo_de(m) for m in motivos]


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


# HU-46 esc. 4: evento encadenado de cada activación. Es también la historia
# de qué versión estaba activa en cada instante (HU-47 esc. 2).
EVENTO_VERSION_ACTIVADA = "MODELO_IA_VERSION_ACTIVADA"
MOTIVO_ARTEFACTO_DISTINTO = "el artefacto desplegado no coincide con el hash registrado"
CODIGO_ARTEFACTO_DISTINTO = "artefacto_distinto_del_registrado"


class ActivarVersionModeloUseCase:
    """HU-46 Escenario 2: activar una versión NO registrada se rechaza — la
    versión activa vigente se mantiene, y el intento queda auditado por el
    llamador (ver ia_router.py) además del evento de trazabilidad que este
    caso de uso genera cuando SÍ tiene éxito.

    `verificar_artefacto` (HU-16 esc. 4) recibe la versión candidata y
    devuelve por qué el artefacto que realmente se cargará NO es el suyo: el
    preventivo con IA_PREVENTIVO_MODO=activo, el legado en otro caso
    (infrastructure/ai/gobierno.py). `hash_artefacto` se conserva por
    compatibilidad: compara con el hash de un único artefacto."""

    def __init__(
        self,
        modelo_repository: IModeloIARepository,
        registrar_hash: RegistrarHashEncadenadoUseCase,
        criterio_principal: str = CRITERIO_POR_INCIDENTE,
        hash_artefacto: Callable[[], str | None] | None = None,
        verificar_artefacto: Callable[[VersionModeloIA], list[str]] | None = None,
        modelo: str = "legado",
    ) -> None:
        self._modelo_repository = modelo_repository
        self._registrar_hash = registrar_hash
        self._criterio = criterio_principal
        self._hash_artefacto = hash_artefacto
        self._verificar_artefacto = verificar_artefacto
        self._modelo = modelo

    async def execute(self, version: str, admin_id: UUID) -> VersionModeloIA:
        # P-12: lo primero es el candado común con /desactivar; la versión
        # activa y la hora del cambio se leen después, ya serializadas.
        await self._modelo_repository.bloquear_cambio_de_version()
        objetivo = await self._modelo_repository.obtener_por_version(version)
        if objetivo is None:
            raise RecursoNoEncontradoError(
                f"La versión {version} no está registrada/aprobada; no puede activarse"
            )
        # HU-46 / HU-18 Escenario 4: sin métricas, H y particiones, o sin
        # cumplir el criterio principal, la versión no entra en producción.
        motivos = motivos_bloqueo_activacion(objetivo, self._criterio)
        # HU-16 esc. 4: no se activa una versión cuyo artefacto no es el que se cargará.
        if self._verificar_artefacto is not None:
            motivos.extend(self._verificar_artefacto(objetivo))
        elif self._hash_artefacto is not None and self._hash_artefacto() != objetivo.model_hash:
            motivos.append(MotivoBloqueo(CODIGO_ARTEFACTO_DISTINTO, MOTIVO_ARTEFACTO_DISTINTO))
        if motivos:
            raise ActivacionBloqueadaError(version, motivos)

        anterior = await self._modelo_repository.obtener_activa()
        cuando = datetime.now(tz=timezone.utc)
        activada = await self._modelo_repository.activar(version, cuando)

        await self._registrar_hash.execute(
            tipo_evento=EVENTO_VERSION_ACTIVADA,
            payload={
                "version_anterior": anterior.version if anterior is not None else None,
                "version_nueva": version,
                "admin_id": str(admin_id),
                "criterio_principal": self._criterio,
                # Qué artefacto aprueba (preventivo o legado) y su hash: el
                # cambio de versión queda encadenado con lo que se cargará.
                "modelo": self._modelo,
                "model_hash": objetivo.model_hash,
            },
            usuario_id=admin_id,
            timestamp=cuando,
        )
        return activada


# P-12 (vuelta atrás que restaura la línea base): evento encadenado de cada
# desactivación. Cierra el periodo de la versión en la historia de HU-47 esc. 2.
EVENTO_VERSION_DESACTIVADA = "MODELO_IA_VERSION_DESACTIVADA"
CODIGO_VERSION_NO_ACTIVA = "version_no_activa"


class VersionNoActivaError(DomainError):
    """La versión existe pero no es la activa (o otra petición ya la desactivó)."""

    codigo = CODIGO_VERSION_NO_ACTIVA

    def __init__(self, version: str) -> None:
        super().__init__(f"La versión {version} no es la versión activa; no hay nada que desactivar")


class DesactivarVersionModeloUseCase:
    """P-12: desactiva la versión ACTIVA del registro sin activar otra. Desde
    ese momento no queda ninguna versión activa, así que el modelo preventivo
    en modo activo no carga (`sin_version_activa_registrada`) y el riesgo
    efectivo vuelve al comportamiento de línea base, el mismo de antes de
    cualquier activación (la regla 2-8 °C con el legado experimental).

    El cambio es condicional y atómico en el repositorio: de dos peticiones
    simultáneas solo una desactiva; la otra recibe VersionNoActivaError (409).
    Además comparte con ActivarVersionModeloUseCase el candado
    bloquear_cambio_de_version(), tomado antes de leer la versión activa y la
    hora: un /activar y un /desactivar simultáneos se aplican uno detrás del
    otro, y las filas, los eventos encadenados y la consulta de HU-47 esc. 2
    terminan diciendo lo mismo."""

    def __init__(
        self,
        modelo_repository: IModeloIARepository,
        registrar_hash: RegistrarHashEncadenadoUseCase,
        modelo: str = "legado",
    ) -> None:
        self._modelo_repository = modelo_repository
        self._registrar_hash = registrar_hash
        self._modelo = modelo

    async def execute(self, version: str, admin_id: UUID, motivo: str) -> VersionModeloIA:
        # Mismo candado que /activar, antes de leer nada y de tomar la hora.
        await self._modelo_repository.bloquear_cambio_de_version()
        objetivo = await self._modelo_repository.obtener_por_version(version)
        if objetivo is None:
            raise RecursoNoEncontradoError(f"La versión {version} no está registrada")
        cuando = datetime.now(tz=timezone.utc)
        desactivada = await self._modelo_repository.desactivar(version, cuando)
        if desactivada is None:
            raise VersionNoActivaError(version)

        # El eslabón guarda quién, cuándo (timestamp del eslabón, igual a
        # desactivada_en), qué versión y por qué; el hash anterior lo encadena.
        await self._registrar_hash.execute(
            tipo_evento=EVENTO_VERSION_DESACTIVADA,
            payload={
                "version": version,
                "admin_id": str(admin_id),
                "motivo": motivo,
                "modelo": self._modelo,
                "model_hash": desactivada.model_hash,
                "activada_en": desactivada.activada_en.isoformat() if desactivada.activada_en else None,
                "version_activa_resultante": None,
            },
            usuario_id=admin_id,
            timestamp=cuando,
        )
        return desactivada


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
        # HU-47 esc. 2: «en el momento de la clasificación» es la hora de la
        # inferencia, no la de captura: una lectura del buffer del nodo puede
        # capturarse antes de activar la versión y clasificarse después.
        momento_clasificacion = lectura.inferido_en or lectura.timestamp
        # La lectura guarda la versión cargada aunque el modelo no llegara a
        # ejecutarse (historial insuficiente, regla directa...). Sin inferencia
        # no hay clasificación que cruzar con el versionado: se responde None,
        # no «no aprobada».
        hubo_inferencia = lectura.modelo_version is not None and lectura.estado_inferencia == ESTADO_COMPLETADA
        if hubo_inferencia:
            version_vigente_en_ese_momento = await self._modelo_repository.obtener_version_activa_en(
                momento_clasificacion
            )
            coincide_con_version_activa_historica = (
                version_vigente_en_ese_momento is not None
                and version_vigente_en_ese_momento.version == lectura.modelo_version
            )

        experimental = bool((lectura.payload or {}).get("ia_experimental"))
        if not hubo_inferencia:
            politica = None
        elif experimental:
            politica = "experimental"
        else:
            politica = "aprobada" if coincide_con_version_activa_historica else "sin_aprobacion_registrada"

        return {
            "lectura_id": lectura.id,
            "device_id": lectura.device_id,
            "timestamp_inferencia": lectura.inferido_en or lectura.timestamp,
            "modelo_version": lectura.modelo_version,
            "vector_features": lectura.vector_features_ia,
            "probabilidades_por_clase": lectura.probabilidades_ia,
            "clasificacion_final": lectura.nivel_riesgo,
            "confianza": lectura.confianza_ia,
            "origen_clasificacion": lectura.origen_clasificacion,
            "version_era_activa_y_aprobada_en_ese_momento": coincide_con_version_activa_historica,
            # Con qué versión registrada se cruzó (None si ninguna estaba activa).
            "version_activa_en_ese_momento": (
                version_vigente_en_ese_momento.version if version_vigente_en_ese_momento else None
            ),
            "estado_inferencia": lectura.estado_inferencia,
            "excursion_confirmada": lectura.excursion_confirmada,
            "riesgo_efectivo": lectura.riesgo_efectivo,
            # HU-34 esc. 4: el mismo estado que muestra el tablero.
            "estado_riesgo": lectura.estado_riesgo(),
            "ia_experimental": experimental,
            # HU-18 esc. 4: horizonte, esquema de características y política vigentes.
            "horizonte_min": version_vigente_en_ese_momento.horizonte_min if version_vigente_en_ese_momento else None,
            "feature_schema_version": (
                version_vigente_en_ese_momento.feature_schema_version if version_vigente_en_ese_momento else None
            ),
            "politica_operativa": politica,
        }
