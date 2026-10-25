from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from src.application.use_cases.registrar_hash_encadenado import (
    RegistrarHashEncadenadoUseCase,
    clave_hmac_trazabilidad,
)
from src.domain.repositories.i_corrupcion_repository import ICorrupcionRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository
from src.domain.value_objects.hash_encadenado import GENESIS_HASH, hash_esperado, timestamp_canonico

_MENSAJE_SIN_CLAVE = "Eslabón HMAC (v3) no verificable: TRAZABILIDAD_HMAC_KEY no está configurada"
_MENSAJE_PAYLOAD_ALTERADO = "Alteración detectada: el payload fue modificado post-registro"
_MENSAJE_ENLACE_ROTO = "Enlace roto: previous_hash no coincide con el hash almacenado del eslabón anterior"

# HU-26 escenario 4: qué certifica (y qué no) el resultado de la verificación.
ALCANCE_VERIFICACION = (
    "Esta verificación certifica únicamente que los registros almacenados no fueron alterados "
    "desde su creación y, de existir una ruptura, en qué punto ocurrió. No constituye una "
    "certificación sanitaria, regulatoria ni de conformidad DIGEMID sobre la conservación de "
    "los medicamentos."
)

EVENTO_CORRUPCION_DETECTADA = "CORRUPCION_CADENA_DETECTADA"
EVENTO_REGISTRO_AISLADO = "REGISTRO_AISLADO_CORRUPCION"

# Detalle por ruptura que se devuelve; el total se informa siempre completo.
MAX_RUPTURAS_DETALLADAS = 50


class EstadoCadena(StrEnum):
    INTEGRA = "integra"
    # Hubo rupturas, todas aisladas por un administrador (HU-47): la cadena
    # vuelve a ser verificable desde el bloque génesis del aislamiento.
    RUPTURA_AISLADA = "ruptura_aislada"
    RUPTURA_DETECTADA = "ruptura_detectada"


class TipoDiscrepancia(StrEnum):
    PAYLOAD_ALTERADO = "payload_alterado"
    ENLACE_ROTO = "enlace_roto"
    NO_VERIFICABLE = "no_verificable"


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
    mensaje: str = _MENSAJE_PAYLOAD_ALTERADO
    chain_id: str = ""
    indice: int = 0
    tipo_discrepancia: TipoDiscrepancia = TipoDiscrepancia.PAYLOAD_ALTERADO
    aislada: bool = False


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
    # HU-26 escenario 2: todas las rupturas, no solo la primera.
    estado: EstadoCadena = EstadoCadena.INTEGRA
    total_rupturas: int = 0
    rupturas: tuple[DetalleInconsistencia, ...] = ()
    # Posteriores a la primera ruptura que verifican contra su propio eslabón
    # anterior: una alteración puntual no invalida lo que vino después.
    registros_posteriores_consistentes: int = 0
    # Con todas las rupturas aisladas: desde cuándo la cadena vuelve a ser íntegra.
    integra_desde: datetime | None = None
    alcance: str = ALCANCE_VERIFICACION


@dataclass(slots=True)
class _EstadoPorCadena:
    anterior: str = GENESIS_HASH
    rupturas: int = 0
    posteriores: int = 0
    posteriores_consistentes: int = 0
    # Hash del eslabón previo a cada ruptura: el evento de emergencia se ancla ahí.
    anclas: dict[str, str] = field(default_factory=dict)
    integra_desde: datetime | None = None


class VerificarIntegridadRegistroUseCase:
    """RF-15/HU-26: verificación O(n) de la cadena de hashes.

    Recorre cada cadena en orden de chain_seq siguiendo su curso REAL: cada
    eslabón se compara con el hash almacenado del anterior. Así una alteración
    puntual se localiza en su registro sin ocultar el estado de los
    posteriores (escenario 2), y los bloques que el propio sistema ancla fuera
    de la secuencia se reconocen como válidos:

    - CORRUPCION_CADENA_DETECTADA (HU-47): anclado al último eslabón íntegro
      previo a la ruptura que notifica.
    - REGISTRO_AISLADO_CORRUPCION (HU-47, aislamiento): bloque génesis de un
      tramo nuevo; si todas las rupturas están aisladas, la cadena se informa
      como `ruptura_aislada` e íntegra desde ese bloque.

    Si detecta una ruptura no aislada, además de reportarla notifica UNA sola
    vez por registro (flag global, snapshot forense y evento de emergencia):
    reintentar la verificación (escenario 3) no duplica avisos y no bloquea la
    ingesta, porque solo lee.
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
        # BE-019: recorrido en streaming, sin materializar la cadena entera en
        # memoria (es una tabla que solo crece, en una instancia de 512 MB).
        clave = clave_hmac_trazabilidad()
        cadenas: dict[str, _EstadoPorCadena] = {}
        rupturas: list[DetalleInconsistencia] = []
        total_rupturas = 0
        notificadas: set[str] = set()
        aisladas: set[str] = set()
        total = 0
        ultimo_hash: str | None = None
        async for registro in self._trazabilidad_repository.iterar_todos_ordenados(chain_id):
            indice = total
            total += 1
            ultimo_hash = registro.hash_actual
            cadena = cadenas.setdefault(registro.chain_id, _EstadoPorCadena())
            calculado = hash_esperado(
                registro.hash_version,
                registro.chain_id,
                registro.previous_hash,
                timestamp_canonico(registro.timestamp),
                registro.payload,
                clave,
            )
            sello_valido = calculado is not None and calculado == registro.hash_actual
            # Como antes: todo lo que sigue a la primera ruptura de su cadena.
            tras_ruptura = cadena.rupturas > 0
            if tras_ruptura:
                cadena.posteriores += 1
            referido = str(registro.payload.get("registro_corrupto_id", ""))

            if sello_valido and registro.tipo_evento == EVENTO_REGISTRO_AISLADO and registro.previous_hash == GENESIS_HASH:
                aisladas.add(referido)
                cadena.integra_desde = _a_utc(registro.timestamp)
                cadena.anterior = registro.hash_actual
                cadena.posteriores_consistentes += tras_ruptura
                continue
            if (
                sello_valido
                and registro.tipo_evento == EVENTO_CORRUPCION_DETECTADA
                and cadena.anclas.get(referido) == registro.previous_hash
            ):
                notificadas.add(referido)
                cadena.anterior = registro.hash_actual
                cadena.posteriores_consistentes += tras_ruptura
                continue

            enlace_ok = registro.previous_hash == cadena.anterior
            if enlace_ok and sello_valido:
                cadena.posteriores_consistentes += tras_ruptura
                cadena.anterior = registro.hash_actual
                continue

            if calculado is None:
                tipo, mensaje = TipoDiscrepancia.NO_VERIFICABLE, _MENSAJE_SIN_CLAVE
            elif not enlace_ok:
                tipo, mensaje = TipoDiscrepancia.ENLACE_ROTO, _MENSAJE_ENLACE_ROTO
            else:
                tipo, mensaje = TipoDiscrepancia.PAYLOAD_ALTERADO, _MENSAJE_PAYLOAD_ALTERADO
            cadena.rupturas += 1
            total_rupturas += 1
            cadena.anclas[str(registro.id)] = cadena.anterior
            if len(rupturas) < MAX_RUPTURAS_DETALLADAS:
                rupturas.append(
                    DetalleInconsistencia(
                        id=registro.id,
                        tipo_evento=registro.tipo_evento,
                        timestamp=registro.timestamp,
                        hash_esperado=calculado or "",
                        hash_almacenado=registro.hash_actual,
                        mensaje=mensaje,
                        chain_id=registro.chain_id,
                        indice=indice,
                        tipo_discrepancia=tipo,
                    )
                )
            # El curso real continúa desde el hash almacenado: el siguiente
            # eslabón se compara con lo que quedó guardado, no con lo ideal.
            cadena.anterior = registro.hash_actual

        if total_rupturas == 0:
            return ResultadoVerificacion(
                integra=True,
                total_registros=total,
                primer_registro_inconsistente=None,
                hash_final=ultimo_hash,
            )

        rupturas = [replace(r, aislada=str(r.id) in aisladas) for r in rupturas]
        pendientes = [r for r in rupturas if not r.aislada]
        todas_aisladas = not pendientes and total_rupturas == len(rupturas)
        principal = pendientes[0] if pendientes else rupturas[0]
        estado_principal = cadenas[principal.chain_id]

        if pendientes and str(principal.id) not in notificadas:
            await self._notificar_corrupcion(
                principal.id,
                principal.chain_id,
                principal,
                estado_principal.anclas[str(principal.id)],
                estado_principal.posteriores,
            )

        integra_desde = None
        if todas_aisladas:
            # Íntegra desde el aislamiento más reciente de las cadenas afectadas.
            integra_desde = max(
                (c.integra_desde for c in cadenas.values() if c.rupturas and c.integra_desde), default=None
            )
        return ResultadoVerificacion(
            integra=False,
            total_registros=total,
            primer_registro_inconsistente=principal.indice,
            detalle_inconsistencia=principal,
            registros_posteriores_afectados=estado_principal.posteriores,
            estado=EstadoCadena.RUPTURA_AISLADA if todas_aisladas else EstadoCadena.RUPTURA_DETECTADA,
            total_rupturas=total_rupturas,
            rupturas=tuple(rupturas),
            registros_posteriores_consistentes=estado_principal.posteriores_consistentes,
            integra_desde=integra_desde,
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
                "tipo_discrepancia": detalle.tipo_discrepancia.value,
                "registros_posteriores_afectados": posteriores,
            },
        )
        await self._registrar_hash.execute(
            tipo_evento=EVENTO_CORRUPCION_DETECTADA,
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

        clave = clave_hmac_trazabilidad()
        registros_dispositivo: list[EstadoRegistroSegmento] = []
        primer_inconsistente: UUID | None = None
        integra = True
        for indice in range(inicio, fin + 1):
            registro = cadena[indice]
            esperado = hash_esperado(
                registro.hash_version,
                registro.chain_id,
                previous_hash,
                timestamp_canonico(registro.timestamp),
                registro.payload,
                clave,
            )
            corrupto = registro.previous_hash != previous_hash or esperado is None or registro.hash_actual != esperado
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
