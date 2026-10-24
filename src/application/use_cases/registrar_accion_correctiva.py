from datetime import datetime, timezone
from uuid import UUID

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.accion_correctiva import AccionCorrectiva
from src.domain.exceptions import DomainError, PermisoDenegadoError, RecursoNoEncontradoError
from src.domain.repositories.i_accion_correctiva_repository import IAccionCorrectivaRepository
from src.domain.repositories.i_alerta_repository import IAlertaRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository
from src.domain.value_objects.rol import Rol


class RegistrarAccionCorrectivaUseCase:
    """RF-10 / HU-27: el usuario responsable registra una acción correctiva
    asociada a una alerta activa, que la transiciona a ATENDIDA (HU-23).

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
    ) -> None:
        self._accion_repository = accion_repository
        self._alerta_repository = alerta_repository
        self._registrar_hash = RegistrarHashEncadenadoUseCase(trazabilidad_repository)

    async def execute(self, alert_id: UUID, usuario_id: UUID, descripcion: str) -> AccionCorrectiva:
        alerta = await self._alerta_repository.obtener_por_id(alert_id)
        if alerta is None:
            raise RecursoNoEncontradoError(f"Alerta {alert_id} no encontrada")

        ahora = datetime.now(tz=timezone.utc)
        gano_la_carrera = await self._alerta_repository.marcar_atendida_si_no_atendida(alert_id, ahora)
        if not gano_la_carrera:
            raise DomainError(
                f"La alerta {alert_id} ya no está vigente: otra acción correctiva la atendió primero."
            )

        accion = AccionCorrectiva(alert_id=alert_id, usuario_id=usuario_id, descripcion=descripcion)
        accion_creada = await self._accion_repository.agregar(accion)

        await self._registrar_hash.execute(
            tipo_evento="ACCION_CORRECTIVA",
            payload={
                # HU-28: identidad de la acción en la evidencia encadenada, para
                # que una rectificación pueda citar el eslabón exacto que corrige.
                "accion_id": str(accion_creada.id),
                "alert_id": str(alert_id),
                "usuario_id": str(usuario_id),
                "descripcion": descripcion,
            },
            device_id=alerta.device_id,
            usuario_id=usuario_id,
        )
        return accion_creada


class CorregirAccionCorrectivaUseCase:
    """HU-28: corregir una justificación previamente registrada crea un
    NUEVO evento que referencia a la acción anterior, en vez de sobrescribir
    su contenido — la original permanece intacta y auditable (en PostgreSQL lo
    garantiza además el trigger append-only de corrective_actions).

    Reglas:
    - BE-020: la rectificación sustituye a la justificación vigente en la
      cronología de la alerta, así que solo puede hacerla su autor o el
      farmacéutico (responsable técnico de la cadena de frío) → 403.
    - Solo se rectifica la acción VIGENTE (la última de su línea): rectificar
      una que ya tiene rectificación bifurcaría la historia → DomainError (409).
    - El eslabón ACCION_CORRECTIVA_RECTIFICADA cita el `hash_actual` del
      eslabón de la acción corregida: el vínculo queda probado
      criptográficamente, no solo por un id en una tabla.
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
        nueva_descripcion: str,
        motivo_rectificacion: str,
        rol_actor: Rol,
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

        rectificacion = AccionCorrectiva(
            alert_id=anterior.alert_id,
            usuario_id=usuario_id,
            descripcion=nueva_descripcion,
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
                "descripcion": nueva_descripcion,
                "corrige_accion_id": str(accion_anterior_id),
                "motivo_rectificacion": motivo_rectificacion,
                "hash_registro_corregido": eslabon.hash_actual if eslabon else None,
                **({} if eslabon else {"vinculo_hash": "accion_anterior_a_hu28_sin_accion_id"}),
            },
            usuario_id=usuario_id,
            device_id=alerta.device_id if alerta is not None else None,
        )
        return creada
