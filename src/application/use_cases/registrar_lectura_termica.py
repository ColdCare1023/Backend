from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.generar_alerta import GenerarAlertaUseCase
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.exceptions import DispositivoNoAutorizadoError, LecturaInvalidaError
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.repositories.i_audit_log_repository import IAuditLogRepository
from src.domain.repositories.i_device_repository import IDeviceRepository
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository

if TYPE_CHECKING:
    from src.infrastructure.notifications.notificacion_service import NotificacionService


class RegistrarLecturaTermicaUseCase:
    """Orquesta el pipeline completo de una lectura entrante (ver README sección 15):
    autorizar dispositivo -> validar -> clasificar riesgo -> persistir ->
    generar alerta -> trazabilidad.

    Control de mínimo privilegio (RNF-05): en modo estricto solo los dispositivos
    provisionados en la tabla `devices` pueden registrar lecturas; un device_id
    desconocido se rechaza. En modo no estricto (desarrollo) el dispositivo se
    registra automáticamente, lo que además preserva la integridad referencial
    del FK `thermal_readings.device_id -> devices.id` en PostgreSQL.
    """

    def __init__(
        self,
        lectura_repository: ILecturaRepository,
        alerta_repository: IAlertaRepository,
        trazabilidad_repository: ITrazabilidadRepository,
        clasificar_riesgo_use_case: ClasificarRiesgoTermicoUseCase,
        device_repository: IDeviceRepository | None = None,
        registro_dispositivos_estricto: bool = False,
        audit_log_repository: IAuditLogRepository | None = None,
        notificacion_service: "NotificacionService | None" = None,
        ventana_normalizacion_alerta_minutos: int | None = None,
    ) -> None:
        self._lectura_repository = lectura_repository
        self._generar_alerta = GenerarAlertaUseCase(
            alerta_repository,
            notificacion_service,
            device_repository=device_repository,
            **(
                {"ventana_normalizacion_minutos": ventana_normalizacion_alerta_minutos}
                if ventana_normalizacion_alerta_minutos is not None
                else {}
            ),
        )
        self._registrar_hash = RegistrarHashEncadenadoUseCase(trazabilidad_repository)
        self._clasificar_riesgo = clasificar_riesgo_use_case
        self._device_repository = device_repository
        self._estricto = registro_dispositivos_estricto
        self._audit_log_repository = audit_log_repository

    async def _autorizar_dispositivo(self, device_id: str) -> None:
        if self._device_repository is None:
            return
        if self._estricto:
            if not await self._device_repository.existe(device_id):
                raise DispositivoNoAutorizadoError(
                    f"El dispositivo '{device_id}' no está registrado; la lectura se rechaza."
                )
        else:
            await self._device_repository.obtener_o_crear(device_id)

    async def execute(self, lectura: LecturaTermica) -> LecturaTermica:
        # HU-05: received_at es el instante en que EL BACKEND recibió el
        # mensaje, distinto de `timestamp` (captured_at, declarado por el
        # nodo). Se estampa aquí porque este use case es el único punto de
        # entrada compartido por el consumidor MQTT y la ingesta REST.
        if lectura.received_at is None:
            lectura.received_at = datetime.now(tz=timezone.utc)

        await self._autorizar_dispositivo(lectura.device_id)

        # B-10: un instante implausible se rechaza ANTES de persistir. Aceptar
        # timestamps futuros permitiría insertar registros que alteran el orden
        # temporal de la evidencia térmica.
        timestamp_valido, motivo = LecturaTermica.es_timestamp_valido(lectura.timestamp)
        if not timestamp_valido:
            if self._audit_log_repository is not None:
                await self._audit_log_repository.registrar(
                    usuario_id=None,
                    accion="LECTURA_RECHAZADA_TIMESTAMP",
                    recurso=f"lecturas/{lectura.device_id}",
                    detalle={
                        "motivo": motivo,
                        "timestamp_recibido": lectura.timestamp.isoformat(),
                        "servidor_utc": datetime.now(tz=timezone.utc).isoformat(),
                    },
                )
            raise LecturaInvalidaError(
                f"Timestamp rechazado para device {lectura.device_id}: {motivo}"
            )

        if not lectura.es_lectura_valida():
            raise LecturaInvalidaError(
                f"Lectura fuera de rango físico plausible para device {lectura.device_id}"
            )

        # Deduplicación/idempotencia (corrige hallazgo B-04, HU-11): un reenvío
        # MQTT (PUBACK perdido, QoS1) con la misma identidad lógica no debe
        # generar una lectura, alerta ni eslabón de hash duplicados. Con
        # boot_id+seq_no (firmware nuevo) esa es la clave real de idempotencia;
        # sin ellos (firmware anterior) se conserva device_id+timestamp.
        if lectura.boot_id is not None and lectura.seq_no is not None:
            existente = await self._lectura_repository.obtener_por_device_boot_seq(
                lectura.device_id, lectura.boot_id, lectura.seq_no
            )
        else:
            existente = await self._lectura_repository.obtener_por_device_y_timestamp(
                lectura.device_id, lectura.timestamp
            )
        if existente is not None:
            return existente

        historial = await self._lectura_repository.listar_recientes_por_device(
            lectura.device_id, limite=20
        )
        clasificacion = self._clasificar_riesgo.execute(lectura, historial)
        lectura.nivel_riesgo = clasificacion.nivel
        # Hallazgo AI-06: la versión del modelo y la confianza quedan en la
        # propia lectura, no solo en el payload de trazabilidad, para poder
        # auditar retroactivamente con qué modelo se clasificó cada registro.
        lectura.modelo_version = self._clasificar_riesgo.modelo_version
        # AIV-07: confianza_ia nunca es 0.0 como valor centinela cuando no
        # hubo inferencia real — se persiste NULL, distinguible de una
        # inferencia real con confianza matemática 0.
        lectura.confianza_ia = None if clasificacion.confianza is None else round(clasificacion.confianza, 4)
        lectura.origen_clasificacion = clasificacion.origen
        lectura.estado_inferencia = clasificacion.estado_inferencia
        lectura.motivo_no_inferencia = clasificacion.motivo_no_inferencia
        # HU-47: evidencia completa de la inferencia (probabilidad por clase
        # y vector de features), para la vista de auditoría por lectura.
        lectura.probabilidades_ia = clasificacion.probabilidades_por_clase
        lectura.vector_features_ia = clasificacion.vector_features

        # HU-18/21/34: excursión confirmada y riesgo efectivo se calculan
        # SIEMPRE aquí, sobre la lectura ya validada como físicamente
        # plausible — independientemente de si la IA pudo clasificar o no.
        # Esto es lo que garantiza que HU-21 sea "independiente de la
        # predicción del modelo": aunque nivel_riesgo (model_class) quede en
        # None por no_clasificable, una temperatura fuera de 2-8 °C sigue
        # marcando excursion_confirmada=True y riesgo_efectivo=critica.
        lectura.excursion_confirmada = lectura.es_excursion_confirmada()
        lectura.riesgo_efectivo = lectura.calcular_riesgo_efectivo()

        lectura_guardada = await self._lectura_repository.agregar(lectura)

        await self._registrar_hash.execute(
            tipo_evento="LECTURA_TERMICA",
            payload={
                "device_id": lectura_guardada.device_id,
                "temperatura_ambiental": lectura_guardada.temperatura_ambiental,
                "humedad_ambiental": lectura_guardada.humedad_ambiental,
                "temperatura_interna": lectura_guardada.temperatura_interna,
                "apertura_refrigerador": lectura_guardada.apertura_refrigerador,
                # None (sensor caído, hallazgo B-05) se serializa explícitamente
                # como tal, nunca como un nivel de riesgo inventado.
                "nivel_riesgo": lectura_guardada.nivel_riesgo.value
                if lectura_guardada.nivel_riesgo is not None
                else None,
                # HU-05: identidad lógica de la lectura declarada por el
                # firmware (None en dispositivos aún no actualizados).
                "reading_id": lectura_guardada.reading_id,
                "boot_id": lectura_guardada.boot_id,
                "seq_no": lectura_guardada.seq_no,
                "time_quality": lectura_guardada.time_quality,
                # HU-18/21/34: model_class (arriba) vs. los dos conceptos
                # distintos que exige el backlog — nunca se colapsan en uno.
                "excursion_confirmada": lectura_guardada.excursion_confirmada,
                "riesgo_efectivo": lectura_guardada.riesgo_efectivo.value
                if lectura_guardada.riesgo_efectivo is not None
                else None,
                # Evidencia de la inferencia (RNF-04 / supervisión de la IA).
                "confianza_ia": lectura_guardada.confianza_ia,
                "origen_clasificacion": clasificacion.origen,
                "modelo_version": lectura_guardada.modelo_version,
                "estado_inferencia": clasificacion.estado_inferencia,
                "motivo_no_inferencia": clasificacion.motivo_no_inferencia,
            },
            device_id=lectura_guardada.device_id,
            timestamp=lectura_guardada.timestamp,
        )

        # AIV-02 + HU-21: se evalúa el episodio de alerta para TODA lectura
        # con riesgo_efectivo resuelto (incluido "normal", que puede cerrar
        # un episodio abierto como evento de recuperación) — no solo para las
        # críticas. Se usa riesgo_efectivo, NO nivel_riesgo/model_class: una
        # excursión confirmada por rango debe generar alerta crítica aunque
        # la IA no haya podido clasificar (no_clasificable, nivel_riesgo=None)
        # — es precisamente lo que hace a HU-21 independiente de la IA.
        if lectura_guardada.riesgo_efectivo is not None and lectura_guardada.id is not None:
            alerta = await self._generar_alerta.execute(
                reading_id=lectura_guardada.id,
                device_id=lectura_guardada.device_id,
                nivel_riesgo=lectura_guardada.riesgo_efectivo,
                timestamp=lectura_guardada.timestamp,
                temperatura_interna=lectura_guardada.temperatura_interna,
            )
            if alerta is not None:
                await self._registrar_hash.execute(
                    tipo_evento="ALERTA_TERMICA",
                    payload={
                        "reading_id": str(lectura_guardada.id),
                        "device_id": lectura_guardada.device_id,
                        "nivel_riesgo": lectura_guardada.riesgo_efectivo.value,
                        "excursion_confirmada": lectura_guardada.excursion_confirmada,
                        "mensaje": alerta.mensaje,
                        "modelo_version": lectura_guardada.modelo_version,
                        "confianza_ia": lectura_guardada.confianza_ia,
                        "episodio_abierto": alerta.episodio_abierto,
                    },
                    device_id=lectura_guardada.device_id,
                )

        return lectura_guardada
