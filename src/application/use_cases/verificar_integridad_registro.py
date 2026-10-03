from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.repositories.i_corrupcion_repository import ICorrupcionRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository
from src.domain.value_objects.hash_encadenado import GENESIS_HASH, HashEncadenado, timestamp_canonico


def _a_utc(valor: datetime) -> datetime:
    """SQLite devuelve datetimes naive aunque la columna sea timezone=True;
    se asumen UTC (mismo criterio que timestamp_canonico y LecturaTermica)."""
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class DetalleInconsistencia:
    id: UUID
    tipo_evento: str
    timestamp: datetime
    hash_esperado: str
    hash_almacenado: str
    mensaje: str = "Alteración detectada: el payload fue modificado post-registro"


@dataclass(frozen=True, slots=True)
class ResultadoVerificacion:
    integra: bool
    total_registros: int
    primer_registro_inconsistente: int | None
    detalle_inconsistencia: DetalleInconsistencia | None = None
    registros_posteriores_afectados: int = 0
    # HU-38 criterio 2: referencia/digest reproducible del estado de la
    # cadena al momento de verificar — el hash_actual del último registro
    # examinado. None si no había registros que verificar.
    hash_final: str | None = None


class VerificarIntegridadRegistroUseCase:
    """RF-15/HU-26: verificación O(n) de la cadena de hashes.

    HU-47 Escenario 1-2: si detecta corrupción, además de reportarla:
    - inserta un evento de emergencia CORRUPCION_CADENA_DETECTADA anclado al
      último hash ÍNTEGRO conocido (no al del bloque corrupto ni al último
      hash almacenado, que puede ya descender de él),
    - activa el flag global `cadena_comprometida` (no detiene la ingesta:
      los registros nuevos se marcan is_after_corruption=True hasta que se
      resuelva, ver ManejarCorrupcionCadenaUseCase),
    - guarda un snapshot forense con la metadata exacta del punto de ruptura.
    """

    def __init__(
        self,
        trazabilidad_repository: ITrazabilidadRepository,
        corrupcion_repository: ICorrupcionRepository | None = None,
        registrar_hash: RegistrarHashEncadenadoUseCase | None = None,
    ) -> None:
        self._trazabilidad_repository = trazabilidad_repository
        self._corrupcion_repository = corrupcion_repository
        self._registrar_hash = registrar_hash

    async def execute(self, chain_id: str | None = None) -> ResultadoVerificacion:
        """HU-26: sin chain_id, recorre TODAS las cadenas a la vez (vista de
        administrador); con chain_id, verifica solo esa cadena de unidad
        monitoreada — es la forma que exige literalmente el criterio de
        aceptación ("verificación completa de una cadena... identificada por
        chain_id"). Cada registro se compara con la fórmula de su propio
        hash_version, así los registros anteriores a la introducción de
        chain_id (hash_version=1) siguen siendo verificables sin recálculo."""
        registros = await self._trazabilidad_repository.listar_todos_ordenados(chain_id)

        previous_hash_por_cadena: dict[str, str] = {}
        ultimo_hash_integro_por_cadena: dict[str, str] = {}
        for indice, registro in enumerate(registros):
            previous_hash_esperado = previous_hash_por_cadena.get(registro.chain_id, GENESIS_HASH)
            esperado = (
                HashEncadenado.calcular_hash(
                    registro.chain_id, previous_hash_esperado, timestamp_canonico(registro.timestamp), registro.payload
                )
                if registro.hash_version >= 2
                else HashEncadenado.calcular_hash_v1(
                    previous_hash_esperado, timestamp_canonico(registro.timestamp), registro.payload
                )
            )
            if registro.previous_hash != previous_hash_esperado or registro.hash_actual != esperado:
                posteriores = [r for r in registros[indice + 1 :] if chain_id is None or r.chain_id == registro.chain_id]
                detalle = DetalleInconsistencia(
                    id=registro.id,
                    tipo_evento=registro.tipo_evento,
                    timestamp=registro.timestamp,
                    hash_esperado=esperado,
                    hash_almacenado=registro.hash_actual,
                )
                ultimo_hash_integro = ultimo_hash_integro_por_cadena.get(registro.chain_id, GENESIS_HASH)
                await self._notificar_corrupcion(
                    registro.id, registro.chain_id, detalle, ultimo_hash_integro, len(posteriores)
                )
                return ResultadoVerificacion(
                    integra=False,
                    total_registros=len(registros),
                    primer_registro_inconsistente=indice,
                    detalle_inconsistencia=detalle,
                    registros_posteriores_afectados=len(posteriores),
                )
            previous_hash_por_cadena[registro.chain_id] = registro.hash_actual
            ultimo_hash_integro_por_cadena[registro.chain_id] = registro.hash_actual

        return ResultadoVerificacion(
            integra=True,
            total_registros=len(registros),
            primer_registro_inconsistente=None,
            hash_final=registros[-1].hash_actual if registros else None,
        )

    async def _notificar_corrupcion(
        self,
        registro_id: UUID,
        chain_id: str,
        detalle: DetalleInconsistencia,
        ultimo_hash_integro: str,
        posteriores: int,
    ) -> None:
        if self._corrupcion_repository is None or self._registrar_hash is None:
            return  # modo solo-lectura (p. ej. reintentos de verificación tras aislar)
        await self._corrupcion_repository.marcar_comprometida()
        await self._corrupcion_repository.guardar_snapshot_forense(
            registro_id,
            {
                "id": str(detalle.id),
                "tipo_evento": detalle.tipo_evento,
                "timestamp": detalle.timestamp.isoformat(),
                "hash_esperado": detalle.hash_esperado,
                "hash_almacenado": detalle.hash_almacenado,
                "registros_posteriores_afectados": posteriores,
            },
        )
        await self._registrar_hash.execute(
            tipo_evento="CORRUPCION_CADENA_DETECTADA",
            payload={
                "registro_corrupto_id": str(detalle.id),
                "hash_esperado": detalle.hash_esperado,
                "hash_almacenado": detalle.hash_almacenado,
                "registros_posteriores_afectados": posteriores,
            },
            timestamp=datetime.now(tz=timezone.utc),
            previous_hash_forzado=ultimo_hash_integro,
            chain_id=chain_id,
        )


@dataclass(frozen=True, slots=True)
class EstadoRegistroSegmento:
    id: UUID
    tipo_evento: str
    timestamp: datetime
    device_id: str | None
    integro: bool


@dataclass(frozen=True, slots=True)
class ResultadoVerificacionSegmento:
    device_id: str
    desde: datetime
    hasta: datetime
    integra: bool
    total_bloques_verificados: int
    registros_del_dispositivo: list[EstadoRegistroSegmento]
    primer_registro_inconsistente: UUID | None = None


class VerificarIntegridadPorDispositivoYPeriodoUseCase:
    """HU-37: a diferencia de VerificarIntegridadRegistroUseCase (cadena
    completa), esta verificación se acota a un dispositivo y periodo.

    Con chain_id=device_id (HU-25), la cadena de ese dispositivo ya no
    entrelaza eventos de otras unidades monitoreadas ni de sistema: el
    segmento se resuelve dentro de listar_todos_ordenados(chain_id), sin la
    limitación previa de "puede incluir bloques intermedios de otro
    dispositivo". Ancla en el registro de esa MISMA cadena inmediatamente
    anterior al periodo (o génesis si el periodo empieza en el primer
    registro de la cadena) y verifica secuencialmente todos los bloques del
    periodo, en orden de chain_seq.
    """

    def __init__(self, trazabilidad_repository: ITrazabilidadRepository) -> None:
        self._trazabilidad_repository = trazabilidad_repository

    async def execute(
        self, device_id: str, desde: datetime, hasta: datetime
    ) -> ResultadoVerificacionSegmento:
        cadena = await self._trazabilidad_repository.listar_todos_ordenados(chain_id=device_id)
        desde, hasta = _a_utc(desde), _a_utc(hasta)

        indices_en_rango = [i for i, r in enumerate(cadena) if desde <= _a_utc(r.timestamp) <= hasta]
        if not indices_en_rango:
            return ResultadoVerificacionSegmento(
                device_id=device_id,
                desde=desde,
                hasta=hasta,
                integra=True,
                total_bloques_verificados=0,
                registros_del_dispositivo=[],
            )
        inicio, fin = indices_en_rango[0], indices_en_rango[-1]

        # Ancla: el hash del registro INMEDIATAMENTE ANTERIOR al segmento
        # dentro de esta misma cadena (o génesis si el segmento empieza en el
        # primer registro de la cadena del dispositivo).
        previous_hash = cadena[inicio - 1].hash_actual if inicio > 0 else GENESIS_HASH

        registros_dispositivo: list[EstadoRegistroSegmento] = []
        primer_inconsistente: UUID | None = None
        integra = True
        for indice in range(inicio, fin + 1):
            registro = cadena[indice]
            esperado = (
                HashEncadenado.calcular_hash(
                    registro.chain_id, previous_hash, timestamp_canonico(registro.timestamp), registro.payload
                )
                if registro.hash_version >= 2
                else HashEncadenado.calcular_hash_v1(
                    previous_hash, timestamp_canonico(registro.timestamp), registro.payload
                )
            )
            corrupto = registro.previous_hash != previous_hash or registro.hash_actual != esperado
            if corrupto:
                integra = False
                if primer_inconsistente is None:
                    primer_inconsistente = registro.id
            registros_dispositivo.append(
                EstadoRegistroSegmento(
                    id=registro.id,
                    tipo_evento=registro.tipo_evento,
                    timestamp=registro.timestamp,
                    device_id=registro.device_id,
                    integro=not corrupto,
                )
            )
            # La cadena sigue su curso real independientemente de si el bloque
            # resultó corrupto: cada eslabón se ancla al anterior tal como fue
            # almacenado, no al que "debería" haber sido.
            previous_hash = registro.hash_actual

        return ResultadoVerificacionSegmento(
            device_id=device_id,
            desde=desde,
            hasta=hasta,
            integra=integra,
            total_bloques_verificados=fin - inicio + 1,
            registros_del_dispositivo=registros_dispositivo,
            primer_registro_inconsistente=primer_inconsistente,
        )
