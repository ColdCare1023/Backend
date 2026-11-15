from datetime import datetime, timezone
from uuid import UUID

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.accion_correctiva import AccionCorrectiva
from src.domain.exceptions import (
    DomainError,
    PermisoDenegadoError,
    RectificacionSinCambiosError,
    RecursoNoEncontradoError,
)
from src.domain.repositories.i_accion_correctiva_repository import IAccionCorrectivaRepository
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.repositories.i_audit_log_repository import IAuditLogRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository
from src.domain.value_objects.estado_alerta import EstadoAlerta
from src.domain.value_objects.rol import Rol

# HU-23: el reconocimiento implícito (acción sobre una alerta PENDIENTE) queda
# como REVISAR_ALERTA con este origen, para distinguirlo del PATCH /revisar.
ORIGEN_ACCION_CORRECTIVA = "accion_correctiva"


class RegistrarAccionCorrectivaUseCase:
    """RF-10 / HU-27: el Químico Farmacéutico responsable registra una acción
    correctiva asociada a una alerta PENDIENTE o RECONOCIDA, que la
    transiciona a ATENDIDA (HU-23).

    HU-23 esc. 1 y 3: el flujo es PENDIENTE -> RECONOCIDA -> ATENDIDA. Si la
    alerta sigue PENDIENTE, quien registra la acción la reconoce primero
    (queda `revisada_por` y `reconocida_en`, y el eslabón REVISAR_ALERTA con
    `origen="accion_correctiva"` antes del de la acción) y luego la atiende.

    Escenario 2 (concurrencia): si otro usuario ya atendió la misma alerta
    (dos pestañas, dos compañeros), la transición ATENDIDA se intenta de
    forma ATÓMICA a nivel de base de datos ANTES de crear la acción — quien
    pierde la carrera recibe DomainError y no llega a registrar nada, en vez
    de sobrescribir en silencio.
    """

    def __init__(
        self,
        accion_repository: IAccionCorrectivaRepository,
        alerta_repository: IAlertaRepository,
        trazabilidad_repository: ITrazabilidadRepository,
        audit_log_repository: IAuditLogRepository,
    ) -> None:
        self._accion_repository = accion_repository
        self._alerta_repository = alerta_repository
        self._trazabilidad_repository = trazabilidad_repository
        self._registrar_hash = RegistrarHashEncadenadoUseCase(trazabilidad_repository)
        self._auditoria = AuditarAccionCriticaUseCase(audit_log_repository, trazabilidad_repository)
        # Si la última ejecución reconoció la alerta (estaba PENDIENTE).
        self.reconocida_implicitamente = False

    async def execute(
        self, alert_id: UUID, usuario_id: UUID, descripcion: str,
        decision: str | None = None, justificacion: str | None = None,
        ip_origen: str | None = None,
    ) -> AccionCorrectiva:
        self.reconocida_implicitamente = False
        alerta = await self._alerta_repository.obtener_por_id(alert_id)
        if alerta is None:
            raise RecursoNoEncontradoError(f"Alerta {alert_id} no encontrada")

        # Orden de bloqueos (HU-25/HU-27): primero la cadena del dispositivo y
        # después la fila de la alerta, igual que la ingesta (eslabón de la
        # lectura -> episodio abierto). Al revés, una lectura del mismo equipo
        # que llega mientras se registra la acción produce un deadlock en
        # PostgreSQL y la acción se aborta con 500.
        await self._trazabilidad_repository.bloquear_cadena(alerta.device_id)
        ahora = datetime.now(tz=timezone.utc)
        if alerta.estado == EstadoAlerta.PENDIENTE and await self._alerta_repository.reconocer_si_pendiente(
            alert_id, usuario_id, ahora
        ):
            # Mismo registro que PATCH /revisar (bitácora + eslabón en la cadena
            # del dispositivo), antes del eslabón de la acción: el auditor ve el
            # orden real PENDIENTE -> RECONOCIDA -> ATENDIDA.
            self.reconocida_implicitamente = True
            await self._auditoria.execute(
                usuario_id=usuario_id,
                accion="REVISAR_ALERTA",
                recurso=f"alertas/{alert_id}",
                detalle={"nivel_riesgo": alerta.nivel_riesgo.value, "origen": ORIGEN_ACCION_CORRECTIVA},
                ip_origen=ip_origen,
                device_id=alerta.device_id,
            )
        # Si otro usuario la reconoció entre la lectura y el UPDATE, se atiende
        # igual desde RECONOCIDA; si ya la atendió, aquí se pierde la carrera.
        gano_la_carrera = await self._alerta_repository.marcar_atendida_si_no_atendida(alert_id, ahora)
        if not gano_la_carrera:
            raise DomainError(
                f"La alerta {alert_id} ya no está vigente: otra acción correctiva la atendió primero."
            )

        accion = AccionCorrectiva(
            alert_id=alert_id, usuario_id=usuario_id, descripcion=descripcion,
            decision=decision, justificacion=justificacion,
        )
        accion_creada = await self._accion_repository.agregar(accion)

        await self._registrar_hash.execute(
            tipo_evento="ACCION_CORRECTIVA",
            payload={
                # HU-28: identidad de la acción en la evidencia encadenada, para
                # que una rectificación pueda citar el eslabón exacto que corrige.
                "accion_id": str(accion_creada.id),
                "alert_id": str(alert_id),
                "usuario_id": str(usuario_id),
                "decision": decision,
                "descripcion": descripcion,
                "justificacion": justificacion,
            },
            device_id=alerta.device_id,
            usuario_id=usuario_id,
        )
        return accion_creada


class CorregirAccionCorrectivaUseCase:
    """HU-28: corregir una decisión o justificación previamente registrada crea
    un NUEVO evento que referencia a la acción anterior, en vez de sobrescribir
    su contenido — la original permanece intacta y auditable (en PostgreSQL lo
    garantiza además el trigger append-only de corrective_actions).

    Reglas:
    - BE-020: la rectificación sustituye a la versión vigente en la cronología
      de la alerta, así que solo puede hacerla su autor o el farmacéutico
      (responsable técnico de la cadena de frío) → 403.
    - Solo se rectifica la acción VIGENTE (la última de su línea): rectificar
      una que ya tiene rectificación bifurcaría la historia → DomainError (409).
    - Lo que no se corrige (decisión, acción o justificación) se hereda de la
      versión corregida; si nada cambia → RectificacionSinCambiosError (422).
    - El eslabón ACCION_CORRECTIVA_RECTIFICADA cita el `hash_actual` del
      eslabón de la acción corregida y lleva la versión completa resultante y
      los campos corregidos: el vínculo y el contenido quedan bajo el hash.
    """

    def __init__(
        self,
        accion_repository: IAccionCorrectivaRepository,
        alerta_repository: IAlertaRepository,
        trazabilidad_repository: ITrazabilidadRepository,
    ) -> None:
        self._accion_repository = accion_repository
        self._alerta_repository = alerta_repository
        self._trazabilidad_repository = trazabilidad_repository
        self._registrar_hash = RegistrarHashEncadenadoUseCase(trazabilidad_repository)

    async def execute(
        self,
        accion_anterior_id: UUID,
        usuario_id: UUID,
        nueva_descripcion: str | None,
        motivo_rectificacion: str,
        rol_actor: Rol,
        nueva_decision: str | None = None,
        nueva_justificacion: str | None = None,
    ) -> AccionCorrectiva:
        anterior = await self._accion_repository.obtener_por_id(accion_anterior_id)
        if anterior is None:
            raise RecursoNoEncontradoError(f"Acción correctiva {accion_anterior_id} no encontrada")
        if anterior.usuario_id != usuario_id and rol_actor != Rol.FARMACEUTICO:
            raise PermisoDenegadoError(
                "Solo el autor de la acción o el farmacéutico responsable puede rectificarla"
            )

        linea = await self._accion_repository.listar_por_alerta(anterior.alert_id)
        if any(a.corrige_accion_id == accion_anterior_id for a in linea):
            raise DomainError(
                "La acción ya fue rectificada: solo se puede rectificar la versión vigente."
            )

        resultante = {
            "decision": nueva_decision if nueva_decision is not None else anterior.decision,
            "descripcion": nueva_descripcion if nueva_descripcion is not None else anterior.descripcion,
            "justificacion": nueva_justificacion if nueva_justificacion is not None else anterior.justificacion,
        }
        corregidos = [campo for campo, valor in resultante.items() if valor != getattr(anterior, campo)]
        if not corregidos:
            raise RectificacionSinCambiosError(
                "La rectificación no modifica la decisión, la acción ni la justificación vigentes."
            )

        rectificacion = AccionCorrectiva(
            alert_id=anterior.alert_id,
            usuario_id=usuario_id,
            descripcion=resultante["descripcion"],
            decision=resultante["decision"],
            justificacion=resultante["justificacion"],
            corrige_accion_id=accion_anterior_id,
            motivo_rectificacion=motivo_rectificacion,
        )
        creada = await self._accion_repository.agregar(rectificacion)

        # Eslabón de la acción corregida: una original se registró como
        # ACCION_CORRECTIVA; una rectificación previa, como _RECTIFICADA. Las
        # acciones anteriores a HU-28 no llevan `accion_id` en su payload: el
        # vínculo queda en null y se deja constancia de por qué.
        eslabon = await self._trazabilidad_repository.buscar_por_payload(
            "ACCION_CORRECTIVA_RECTIFICADA" if anterior.corrige_accion_id else "ACCION_CORRECTIVA",
            "accion_id",
            str(accion_anterior_id),
        )

        # HU-25/HU-28: se encadena en la cadena DEL DISPOSITIVO de la alerta
        # original — es lo que permite a un auditor reconstruir el ciclo
        # completo de una alerta (reconocimiento, acción, rectificaciones)
        # verificando una sola cadena por chain_id.
        alerta = await self._alerta_repository.obtener_por_id(anterior.alert_id)
        await self._registrar_hash.execute(
            tipo_evento="ACCION_CORRECTIVA_RECTIFICADA",
            payload={
                "accion_id": str(creada.id),
                "alert_id": str(anterior.alert_id),
                "usuario_id": str(usuario_id),
                **resultante,
                "campos_corregidos": corregidos,
                "corrige_accion_id": str(accion_anterior_id),
                "motivo_rectificacion": motivo_rectificacion,
                "hash_registro_corregido": eslabon.hash_actual if eslabon else None,
                **({} if eslabon else {"vinculo_hash": "accion_anterior_a_hu28_sin_accion_id"}),
            },
            usuario_id=usuario_id,
            device_id=alerta.device_id if alerta is not None else None,
        )
        return creada
