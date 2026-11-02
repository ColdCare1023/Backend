import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.clasificar_riesgo_termico import ClasificarRiesgoTermicoUseCase
from src.application.use_cases.generar_alerta import AvisoExcursion, GenerarAlertaUseCase
from src.application.use_cases.incidentes_lectura import abrir_incidente, cerrar_incidente
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.application.use_cases.vigilar_sin_datos import AvisoSinDatos, CambioIncidente
from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.exceptions import (
    CredencialEnCuarentenaError,
    CredencialRevocadaError,
    DispositivoNoAutorizadoError,
    LecturaInvalidaError,
)
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.repositories.i_audit_log_repository import IAuditLogRepository
from src.domain.repositories.i_device_repository import IDeviceRepository
from src.domain.repositories.i_incidente_repository import TIPO_SENSOR_FAULT, IIncidenteRepository
from src.domain.repositories.i_lectura_repository import ILecturaRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository
from src.domain.value_objects.nivel_riesgo import NivelRiesgo
from src.domain.value_objects.rango_termico import RANGO_TERMICO_BPA
from src.infrastructure.ai.random_forest_service import (
    ESTADO_FALLIDA,
    ORIGEN_MODELO,
    ORIGEN_REGLA_SALVAGUARDA,
    ResultadoInferencia,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.application.use_cases.predecir_riesgo_preventivo import PredecirRiesgoPreventivoUseCase


async def credencial_revocada(device_repository: IDeviceRepository, device_id: str) -> bool:
    """HU-44: True si al dispositivo se le emitió una credencial MQTT y ya no está
    vigente. Un dispositivo al que nunca se le emitió una (hash ausente) no cuenta
    como revocado: ese caso lo gobierna el registro estricto."""
    credencial = await device_repository.obtener_credencial(device_id)
    return bool(credencial and credencial["mqtt_token_hash"] and not credencial["mqtt_token_activo"])


async def credencial_en_cuarentena(device_repository: IDeviceRepository, device_id: str) -> bool:
    """HU-44: True mientras dure una rotación por compromiso: desde que se rota
    hasta que, con la consola confirmada, el nodo prueba la posesión del token
    nuevo. En esa ventana el broker puede seguir aceptando el token anterior."""
    credencial = await device_repository.obtener_credencial(device_id)
    # La retención dura mientras la marca siga puesta: confirmar la consola no
    # basta, el nodo debe probar que tiene el token nuevo (prueba_posesion.py).
    return bool(credencial and credencial.get("rotacion_por_compromiso"))


def _motivo_fallo_ia(exc: Exception) -> str:
    """Motivo breve, sin trazas ni mensajes del modelo (motivo_no_inferencia)."""
    return "timeout_inferencia" if isinstance(exc, asyncio.TimeoutError) else "excepcion_inferencia"


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
        ventana_normalizacion_alerta_minutos: int | None = None,
        prediccion_preventiva: "PredecirRiesgoPreventivoUseCase | None" = None,
        antiguedad_maxima_horas: int | None = None,
        ia_legado_modo: str = "activo",
        ia_timeout_s: float | None = None,
        ia_prueba_retardo_s: float = 0.0,
        ia_prueba_excepcion: bool = False,
        incidente_repository: IIncidenteRepository | None = None,
    ) -> None:
        # Incidente `sensor_fault` (outcome 3): se abre/cierra con la telemetría.
        self._incidentes = incidente_repository
        self._trazabilidad = trazabilidad_repository
        self.cambios_incidente: list[CambioIncidente] = []
        self.avisos_incidente: list[AvisoSinDatos] = []
        # La alerta crítica no depende de la IA: tope de tiempo y excepciones
        # absorbidas (None = sin tope, el comportamiento anterior).
        self._ia_timeout_s = ia_timeout_s
        self._ia_prueba_retardo_s = ia_prueba_retardo_s
        self._ia_prueba_excepcion = ia_prueba_excepcion
        self._lectura_repository = lectura_repository
        self._antiguedad_maxima = timedelta(hours=antiguedad_maxima_horas) if antiguedad_maxima_horas else None
        self._generar_alerta = GenerarAlertaUseCase(
            alerta_repository,
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
        # Modelo preventivo temporal (docs/ml/06_BACKEND_INTEGRATION.md).
        # None = desactivado: el flujo es exactamente el anterior.
        self._prediccion_preventiva = prediccion_preventiva
        # HU-20: IA_LEGADO_MODO (ver Settings.ia_legado_modo).
        self._ia_legado_modo = ia_legado_modo

    @property
    def avisos_pendientes(self) -> list[AvisoExcursion]:
        """HU-53: avisos de excursión crítica decididos en esta ejecución. Quien
        hace COMMIT los entrega al despachador DESPUÉS del commit (D2)."""
        return self._generar_alerta.avisos_pendientes

    async def _autorizar_dispositivo(self, device_id: str, *, es_manual: bool = False) -> None:
        if self._device_repository is None:
            return
        if self._estricto:
            if not await self._device_repository.existe(device_id):
                raise DispositivoNoAutorizadoError(
                    f"El dispositivo '{device_id}' no está registrado; la lectura se rechaza."
                )
        else:
            await self._device_repository.obtener_o_crear(device_id)
        # BE-003: en ambos modos, un dispositivo dado de baja ya no aporta
        # evidencia (puede tratarse de un equipo retirado o sustraído).
        if not await self._device_repository.esta_activo(device_id):
            raise DispositivoNoAutorizadoError(
                f"El dispositivo '{device_id}' está dado de baja; la lectura se rechaza."
            )
        # HU-44: credencial revocada = la telemetría del nodo no se persiste, aunque
        # el broker todavía lo deje conectar. Una lectura manual la registra una
        # persona y no depende de la credencial del nodo.
        if not es_manual and await credencial_revocada(self._device_repository, device_id):
            raise CredencialRevocadaError(
                f"La credencial del dispositivo '{device_id}' está revocada; la lectura se rechaza."
            )
        if not es_manual and await credencial_en_cuarentena(self._device_repository, device_id):
            raise CredencialEnCuarentenaError(
                f"Rotación por compromiso de '{device_id}' pendiente en el broker; la lectura se retiene."
            )

    async def _evaluar_sensor_fault(self, lectura: LecturaTermica, historial: list) -> None:
        """Segunda lectura seguida con el DS18B20 en falla abre `sensor_fault`;
        la siguiente válida lo cierra. Las lecturas manuales no cuentan."""
        if self._incidentes is None or lectura.es_manual:
            return
        auditoria = AuditarAccionCriticaUseCase(self._audit_log_repository, self._trazabilidad)
        ahora = datetime.now(timezone.utc)
        if lectura.temperatura_interna is None:
            previa = historial[0] if historial else None  # la más reciente (orden descendente)
            if previa is None or previa.temperatura_interna is not None:
                return  # primera falla: puede ser aislada
            abierto = await abrir_incidente(
                self._incidentes, auditoria, lectura.device_id, TIPO_SENSOR_FAULT, ahora,
                {
                    "captured_at": lectura.timestamp.isoformat(),
                    "boot_id": lectura.boot_id,
                    "seq_no": lectura.seq_no,
                    "lecturas_seguidas": 2,
                },
                ultimo_reporte_en=lectura.timestamp,
            )
            if abierto is not None:
                self.cambios_incidente.append(abierto[0])
                self.avisos_incidente.append(abierto[1])
            return
        cierre = await cerrar_incidente(self._incidentes, auditoria, lectura.device_id, TIPO_SENSOR_FAULT, ahora)
        if cierre is not None:
            self.cambios_incidente.append(cierre)

    async def _con_limite(self, corrutina, inyectar: bool = True):
        """Espera `corrutina` como mucho `ia_timeout_s` y aplica la inyección de
        fallos de prueba (retardo o excepción) antes de ella."""
        async def _ejecutar():
            if inyectar and self._ia_prueba_retardo_s:
                await asyncio.sleep(self._ia_prueba_retardo_s)
            if inyectar and self._ia_prueba_excepcion:
                raise RuntimeError("fallo de inferencia inyectado")
            return await corrutina

        try:
            return await asyncio.wait_for(_ejecutar(), self._ia_timeout_s)
        finally:
            corrutina.close()  # no queda sin esperar si el fallo o el tope se adelantaron

    async def _clasificar_con_limite(
        self, lectura: LecturaTermica, historial: list, solo_regla: bool = False
    ) -> ResultadoInferencia:
        """Clasifica en un hilo, con tope de tiempo. Si vence o lanza, la lectura
        queda `fallida` y la regla determinista sigue decidiendo la excursión."""
        async def _en_hilo():
            return await asyncio.to_thread(self._clasificar_riesgo.execute, lectura, historial, solo_regla)

        try:
            return await self._con_limite(_en_hilo(), inyectar=not solo_regla)
        except Exception as exc:
            motivo = _motivo_fallo_ia(exc)
            logger.warning("Clasificación fallida para %s: %s", lectura.device_id, motivo)
            return ResultadoInferencia(
                nivel=None,
                confianza=None,
                origen=ORIGEN_REGLA_SALVAGUARDA,
                estado_inferencia=ESTADO_FALLIDA,
                motivo_no_inferencia=motivo,
            )

    async def execute(self, lectura: LecturaTermica) -> LecturaTermica:
        # HU-05: received_at es el instante en que EL BACKEND recibió el
        # mensaje, distinto de `timestamp` (captured_at, declarado por el
        # nodo). Se estampa aquí porque este use case es el único punto de
        # entrada compartido por el consumidor MQTT y la ingesta REST.
        if lectura.received_at is None:
            lectura.received_at = datetime.now(tz=timezone.utc)

        await self._autorizar_dispositivo(lectura.device_id, es_manual=lectura.es_manual)

        # B-10: un instante implausible se rechaza ANTES de persistir. Aceptar
        # timestamps futuros permitiría insertar registros que alteran el orden
        # temporal de la evidencia térmica.
        timestamp_valido, motivo = LecturaTermica.es_timestamp_valido(
            lectura.timestamp, origen=lectura.origen, antiguedad_maxima=self._antiguedad_maxima
        )
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
        #
        # BE-001: una lectura manual nunca participa de esta idempotencia. Su
        # identidad la genera el servidor, y los repositorios solo comparan
        # contra telemetría de dispositivo: una persona no puede ocupar por
        # adelantado la identidad de una lectura real del nodo.
        existente = None
        if not lectura.es_manual:
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
            lectura.device_id, limite=20, solo_dispositivo=not lectura.es_manual
        )
        # La clasificación con el modelo no corre si la regla directa ya declaró la
        # excursión (RF-08): el aviso no gasta su presupuesto de tiempo en ella.
        fuera_de_rango = lectura.es_excursion_confirmada()
        clasificacion = await self._clasificar_con_limite(lectura, historial, solo_regla=fuera_de_rango)
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

        # Predicción preventiva temporal: en sombra solo se registra; en modo
        # activo (modelo aprobado) reemplaza la clasificación anterior. Nunca
        # interviene en la excursión confirmada, que se calcula justo abajo.
        prediccion = None
        reemplazada = False
        if self._prediccion_preventiva is not None:
            try:
                # Fuera de rango el servicio responde `excursion_en_curso` sin invocar al
                # modelo; solo se conserva el tope de tiempo (sin inyección de pruebas).
                prediccion = await self._con_limite(
                    self._prediccion_preventiva.predecir(lectura), inyectar=not fuera_de_rango
                )
            except Exception as exc:  # nunca propagar: la regla 2-8 °C no espera al modelo
                prediccion = None
                motivo = _motivo_fallo_ia(exc)
                logger.warning("Predicción preventiva fallida para %s: %s", lectura.device_id, motivo)
                if self._prediccion_preventiva.modo == "activo":
                    lectura.nivel_riesgo = None
                    lectura.estado_inferencia = ESTADO_FALLIDA
                    lectura.motivo_no_inferencia = f"preventivo_{motivo}"[:100]
            reemplazada = self._prediccion_preventiva.aplicar_a_lectura(lectura, prediccion)

        # HU-18/21/34: excursión confirmada y riesgo efectivo se calculan
        # SIEMPRE aquí, sobre la lectura ya validada como físicamente
        # plausible — independientemente de si la IA pudo clasificar o no.
        # Esto es lo que garantiza que HU-21 sea "independiente de la
        # predicción del modelo": aunque nivel_riesgo (model_class) quede en
        # None por no_clasificable, una temperatura fuera de 2-8 °C sigue
        # marcando excursion_confirmada=True y riesgo_efectivo=critica.
        lectura.excursion_confirmada = lectura.es_excursion_confirmada()
        lectura.riesgo_efectivo = lectura.calcular_riesgo_efectivo()
        # Si la IA falló (tiempo agotado o excepción) no hay nivel de modelo, pero
        # una lectura DENTRO de 2-8 °C sigue siendo normal: sin esto no llegaría a
        # la gestión de alertas y un episodio abierto no se cerraría nunca.
        if (
            lectura.riesgo_efectivo is None
            and lectura.estado_inferencia == ESTADO_FALLIDA
            and lectura.temperatura_interna is not None
            and RANGO_TERMICO_BPA.contiene(lectura.temperatura_interna)
        ):
            lectura.riesgo_efectivo = NivelRiesgo.NORMAL
        # HU-20: el modelo legado (datos sintéticos) en modo experimental deja su
        # clase como evidencia marcada, pero no abre alertas preventivas: sin
        # excursión, el riesgo efectivo es el de la salvaguarda determinista.
        if (
            not reemplazada
            and self._ia_legado_modo == "experimental"
            and clasificacion.probabilidades_por_clase is not None
        ):
            lectura.payload = {**(lectura.payload or {}), "ia_experimental": True}
            if clasificacion.origen == ORIGEN_MODELO and not lectura.excursion_confirmada:
                lectura.riesgo_efectivo = clasificacion.nivel_regla

        lectura_guardada, creada = await self._lectura_repository.agregar_si_no_existe(lectura)
        if not creada:
            # Otra transacción persistió la misma lectura entre la consulta y
            # la inserción: ya tiene su eslabón y su alerta; esta no agrega nada.
            return lectura_guardada
        if prediccion is not None and lectura_guardada.id is not None:
            await self._prediccion_preventiva.registrar(prediccion, lectura_guardada.id)

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
                "ia_experimental": bool((lectura_guardada.payload or {}).get("ia_experimental")),
                # BE-001: la evidencia declara si la lectura es telemetría del
                # nodo o una carga manual, y quién la hizo.
                "origen": lectura_guardada.origen,
                "registrado_por": str(lectura_guardada.registrado_por)
                if lectura_guardada.registrado_por is not None
                else None,
            },
            device_id=lectura_guardada.device_id,
            usuario_id=lectura_guardada.registrado_por,
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
                received_at=lectura_guardada.received_at,
                time_quality=lectura_guardada.time_quality,
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

        for cerrada in self._generar_alerta.cierres:
            await self._registrar_hash.execute(
                tipo_evento="ALERTA_CERRADA",
                payload={
                    "alerta_id": str(cerrada.id),
                    "device_id": cerrada.device_id,
                    "nivel_riesgo": cerrada.nivel_riesgo.value,
                    "iniciada_en": cerrada.iniciada_en.isoformat() if cerrada.iniciada_en else None,
                    "cerrada_en": cerrada.cerrada_en.isoformat() if cerrada.cerrada_en else None,
                    "duracion_s": cerrada.duracion_s,
                    "temperatura_pico": cerrada.temperatura_pico,
                    "historica": cerrada.historica,
                },
                device_id=cerrada.device_id,
            )
        self._generar_alerta.cierres.clear()
        await self._evaluar_sensor_fault(lectura_guardada, historial)
        return lectura_guardada
