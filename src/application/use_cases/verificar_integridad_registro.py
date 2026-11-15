from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from src.application.use_cases.registrar_hash_encadenado import (
    RegistrarHashEncadenadoUseCase,
    clave_hmac_trazabilidad,
)
from src.domain.entities.registro_trazabilidad import RegistroTrazabilidad
from src.domain.repositories.i_corrupcion_repository import ICorrupcionRepository
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository
from src.domain.value_objects.hash_encadenado import (
    GENESIS_HASH,
    HASH_FORMULA_VERSION_HMAC,
    hash_esperado,
    timestamp_canonico,
)

_MENSAJE_SIN_CLAVE = (
    "Eslabón HMAC (v3) no verificable: TRAZABILIDAD_HMAC_KEY no está configurada. "
    "La verificación no concluye: no se informa como íntegra ni como alterada."
)
_MENSAJE_PAYLOAD_ALTERADO = "Alteración detectada: el payload fue modificado post-registro"
_MENSAJE_ENLACE_ROTO = "Enlace roto: previous_hash no coincide con el hash almacenado del eslabón anterior"

# HU-26 escenario 4: qué comprueba (y qué no) el resultado de la verificación.
# Redacción del criterio de aceptación: comprueba consistencia y detecta las
# alteraciones identificables bajo el mecanismo empleado; no certifica.
ALCANCE_BASE = (
    "Esta verificación comprueba la consistencia de la cadena de trazabilidad y detecta las "
    "alteraciones identificables bajo el mecanismo de encadenamiento y las condiciones de seguridad "
    "empleados; de existir una ruptura, informa en qué punto ocurrió. El resultado no equivale a "
    "inmutabilidad, no certifica por sí mismo toda la historia del dato y no constituye una "
    "certificación sanitaria, regulatoria ni de conformidad DIGEMID sobre la conservación de los "
    "medicamentos."
)
ALCANCE_SIN_CLAVE = (
    "Eslabones v1/v2 (SHA-256 sin clave): una reescritura completa de la cadena, con recálculo de "
    "todos los hashes posteriores, podría no detectarse."
)
ALCANCE_HMAC = (
    "Eslabones v3 (HMAC-SHA256): la protección supone que la clave de trazabilidad, guardada fuera "
    "de la base de datos, se mantenga secreta."
)
# Texto completo (ambos mecanismos): el que se declara cuando no se sabe qué
# versiones intervienen. Se conserva el nombre por compatibilidad.
ALCANCE_VERIFICACION = f"{ALCANCE_BASE} {ALCANCE_SIN_CLAVE} {ALCANCE_HMAC}"


def alcance_para_versiones(versiones: Iterable[int]) -> str:
    """HU-26 esc. 4: alcance según las versiones de hash de los eslabones
    realmente verificados (sin eslabones, se declaran ambos mecanismos)."""
    presentes = set(versiones)
    if not presentes:
        return ALCANCE_VERIFICACION
    partes = [ALCANCE_BASE]
    if any(v < HASH_FORMULA_VERSION_HMAC for v in presentes):
        partes.append(ALCANCE_SIN_CLAVE)
    if any(v >= HASH_FORMULA_VERSION_HMAC for v in presentes):
        partes.append(ALCANCE_HMAC)
    return " ".join(partes)


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
    # HU-26 esc. 1: algún eslabón v3 no pudo verificarse (sin clave HMAC) y no
    # hay rupturas demostradas. No es íntegra ni corrupta: no concluyó.
    NO_CONCLUIDA = "no_concluida"


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
    # cadena al momento de verificar — el hash_actual (64 hex) del último
    # registro examinado. None si no había registros que verificar.
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
    # HU-26 esc. 1: eslabones v3 que no se pudieron verificar (sin clave).
    total_no_verificables: int = 0
    no_verificables: tuple[DetalleInconsistencia, ...] = ()
    # Versiones de hash de los eslabones examinados (alcance por versión).
    versiones_hash: tuple[int, ...] = ()
    chain_id: str | None = None


@dataclass(slots=True)
class _EstadoPorCadena:
    anterior: str = GENESIS_HASH
    rupturas: int = 0
    posteriores: int = 0
    posteriores_consistentes: int = 0
    # Hash del eslabón previo a cada ruptura: el evento de emergencia se ancla ahí.
    anclas: dict[str, str] = field(default_factory=dict)
    integra_desde: datetime | None = None


def _detalle(
    registro: RegistroTrazabilidad,
    calculado: str | None,
    indice: int,
    tipo: TipoDiscrepancia,
) -> DetalleInconsistencia:
    mensaje = {
        TipoDiscrepancia.NO_VERIFICABLE: _MENSAJE_SIN_CLAVE,
        TipoDiscrepancia.ENLACE_ROTO: _MENSAJE_ENLACE_ROTO,
        TipoDiscrepancia.PAYLOAD_ALTERADO: _MENSAJE_PAYLOAD_ALTERADO,
    }[tipo]
    return DetalleInconsistencia(
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


def _anotar_no_verificable(
    detalles: list[DetalleInconsistencia], registro: RegistroTrazabilidad, indice: int
) -> None:
    if len(detalles) < MAX_RUPTURAS_DETALLADAS:
        detalles.append(_detalle(registro, None, indice, TipoDiscrepancia.NO_VERIFICABLE))


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

    Un eslabón v3 sin clave HMAC disponible NO es una ruptura: su enlace se
    comprueba igual, pero su sello no; la verificación se informa como
    `no_concluida` (HU-26 esc. 1) y no dispara la notificación de corrupción.

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
        no_verificables: list[DetalleInconsistencia] = []
        total_rupturas = 0
        total_no_verificables = 0
        notificadas: set[str] = set()
        aisladas: set[str] = set()
        versiones: set[int] = set()
        total = 0
        ultimo_hash: str | None = None
        async for registro in self._trazabilidad_repository.iterar_todos_ordenados(chain_id):
            indice = total
            total += 1
            ultimo_hash = registro.hash_actual
            versiones.add(registro.hash_version)
            cadena = cadenas.setdefault(registro.chain_id, _EstadoPorCadena())
            calculado = hash_esperado(
                registro.hash_version,
                registro.chain_id,
                registro.previous_hash,
                timestamp_canonico(registro.timestamp),
                registro.payload,
                clave,
            )
            # True: sello verificado; False: sello alterado; None: sin clave.
            sello = None if calculado is None else calculado == registro.hash_actual
            # Como antes: todo lo que sigue a la primera ruptura de su cadena.
            tras_ruptura = cadena.rupturas > 0
            if tras_ruptura:
                cadena.posteriores += 1
            referido = str(registro.payload.get("registro_corrupto_id", ""))

            if (
                sello is not False
                and registro.tipo_evento == EVENTO_REGISTRO_AISLADO
                and registro.previous_hash == GENESIS_HASH
            ):
                aisladas.add(referido)
                cadena.integra_desde = _a_utc(registro.timestamp)
                cadena.anterior = registro.hash_actual
                if sello is None:
                    _anotar_no_verificable(no_verificables, registro, indice)
                    total_no_verificables += 1
                else:
                    cadena.posteriores_consistentes += tras_ruptura
                continue
            if (
                sello is not False
                and registro.tipo_evento == EVENTO_CORRUPCION_DETECTADA
                and cadena.anclas.get(referido) == registro.previous_hash
            ):
                notificadas.add(referido)
                cadena.anterior = registro.hash_actual
                if sello is None:
                    _anotar_no_verificable(no_verificables, registro, indice)
                    total_no_verificables += 1
                else:
                    cadena.posteriores_consistentes += tras_ruptura
                continue

            enlace_ok = registro.previous_hash == cadena.anterior
            if enlace_ok and sello is True:
                cadena.posteriores_consistentes += tras_ruptura
                cadena.anterior = registro.hash_actual
                continue
            if enlace_ok and sello is None:
                # El enlace con lo almacenado se comprobó; el sello HMAC no se
                # puede comprobar sin la clave: no se da por íntegro ni por roto.
                _anotar_no_verificable(no_verificables, registro, indice)
                total_no_verificables += 1
                cadena.anterior = registro.hash_actual
                continue

            # Un enlace roto se detecta aunque falte la clave: no depende del sello.
            tipo = TipoDiscrepancia.ENLACE_ROTO if not enlace_ok else TipoDiscrepancia.PAYLOAD_ALTERADO
            cadena.rupturas += 1
            total_rupturas += 1
            cadena.anclas[str(registro.id)] = cadena.anterior
            if len(rupturas) < MAX_RUPTURAS_DETALLADAS:
                rupturas.append(_detalle(registro, calculado, indice, tipo))
            # El curso real continúa desde el hash almacenado: el siguiente
            # eslabón se compara con lo que quedó guardado, no con lo ideal.
            cadena.anterior = registro.hash_actual

        comunes = {
            "total_registros": total,
            "hash_final": ultimo_hash,
            "alcance": alcance_para_versiones(versiones),
            "total_no_verificables": total_no_verificables,
            "no_verificables": tuple(no_verificables),
            "versiones_hash": tuple(sorted(versiones)),
            "chain_id": chain_id,
        }

        if total_rupturas == 0:
            if total_no_verificables == 0:
                return ResultadoVerificacion(integra=True, primer_registro_inconsistente=None, **comunes)
            primero = no_verificables[0]
            return ResultadoVerificacion(
                integra=False,
                primer_registro_inconsistente=primero.indice,
                detalle_inconsistencia=primero,
                estado=EstadoCadena.NO_CONCLUIDA,
                **comunes,
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

        if pendientes or not todas_aisladas:
            estado = EstadoCadena.RUPTURA_DETECTADA
        elif total_no_verificables:
            # Rupturas aisladas, pero el tramo posterior no se pudo verificar.
            estado = EstadoCadena.NO_CONCLUIDA
        else:
            estado = EstadoCadena.RUPTURA_AISLADA

        integra_desde = None
        if estado == EstadoCadena.RUPTURA_AISLADA:
            # Íntegra desde el aislamiento más reciente de las cadenas afectadas.
            integra_desde = max(
                (c.integra_desde for c in cadenas.values() if c.rupturas and c.integra_desde), default=None
            )
        return ResultadoVerificacion(
            integra=False,
            primer_registro_inconsistente=principal.indice,
            detalle_inconsistencia=principal,
            registros_posteriores_afectados=estado_principal.posteriores,
            estado=estado,
            total_rupturas=total_rupturas,
            rupturas=tuple(rupturas),
            registros_posteriores_consistentes=estado_principal.posteriores_consistentes,
            integra_desde=integra_desde,
            **comunes,
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


class EstadoSegmento(StrEnum):
    INTEGRA = "integra"
    CORRUPTA = "corrupta"
    NO_CONCLUIDA = "no_concluida"


class EstadoRegistro(StrEnum):
    INTEGRO = "integro"
    CORRUPTO = "corrupto"
    NO_VERIFICABLE = "no_verificable"


@dataclass(frozen=True, slots=True)
class EstadoRegistroSegmento:
    id: UUID
    tipo_evento: str
    timestamp: datetime
    device_id: str | None
    integro: bool
    # HU-37 esc. 2: "Corrupta" con su ID y timestamp; sin clave, no verificable.
    estado: EstadoRegistro = EstadoRegistro.INTEGRO
    tipo_discrepancia: TipoDiscrepancia | None = None
    chain_seq: int | None = None


@dataclass(frozen=True, slots=True)
class ResultadoVerificacionSegmento:
    device_id: str
    desde: datetime
    hasta: datetime
    integra: bool
    total_bloques_verificados: int
    registros_del_dispositivo: list[EstadoRegistroSegmento]
    primer_registro_inconsistente: UUID | None = None
    estado: EstadoSegmento = EstadoSegmento.INTEGRA
    total_corruptos: int = 0
    total_no_verificables: int = 0
    versiones_hash: tuple[int, ...] = ()
    # Hash del eslabón inmediatamente anterior al periodo (o génesis).
    hash_ancla: str = GENESIS_HASH
    alcance: str = ALCANCE_VERIFICACION


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

    Igual que la verificación completa, reconoce los bloques que el propio
    sistema ancla fuera de la secuencia (aviso de corrupción anclado al último
    eslabón íntegro y génesis de aislamiento), para no marcarlos como corruptos.
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
        ancla = cadena[inicio - 1].hash_actual if inicio > 0 else GENESIS_HASH
        previous_hash = ancla
        # Aviso de corrupción: se ancla al eslabón previo al registro corrupto.
        posicion = {str(r.id): i for i, r in enumerate(cadena)}

        clave = clave_hmac_trazabilidad()
        registros_dispositivo: list[EstadoRegistroSegmento] = []
        primer_inconsistente: UUID | None = None
        corruptos = no_verificables = 0
        versiones: set[int] = set()
        for indice in range(inicio, fin + 1):
            registro = cadena[indice]
            versiones.add(registro.hash_version)
            calculado = hash_esperado(
                registro.hash_version,
                registro.chain_id,
                registro.previous_hash,
                timestamp_canonico(registro.timestamp),
                registro.payload,
                clave,
            )
            sello = None if calculado is None else calculado == registro.hash_actual
            enlace_ok = registro.previous_hash == previous_hash or self._anclaje_del_sistema(
                registro, cadena, posicion
            )
            if not enlace_ok:
                estado, tipo = EstadoRegistro.CORRUPTO, TipoDiscrepancia.ENLACE_ROTO
            elif sello is False:
                estado, tipo = EstadoRegistro.CORRUPTO, TipoDiscrepancia.PAYLOAD_ALTERADO
            elif sello is None:
                estado, tipo = EstadoRegistro.NO_VERIFICABLE, TipoDiscrepancia.NO_VERIFICABLE
            else:
                estado, tipo = EstadoRegistro.INTEGRO, None
            if estado == EstadoRegistro.CORRUPTO:
                corruptos += 1
                if primer_inconsistente is None:
                    primer_inconsistente = registro.id
            elif estado == EstadoRegistro.NO_VERIFICABLE:
                no_verificables += 1
            registros_dispositivo.append(
                EstadoRegistroSegmento(
                    id=registro.id,
                    tipo_evento=registro.tipo_evento,
                    timestamp=registro.timestamp,
                    device_id=registro.device_id,
                    integro=estado == EstadoRegistro.INTEGRO,
                    estado=estado,
                    tipo_discrepancia=tipo,
                    chain_seq=registro.chain_seq,
                )
            )
            # La cadena sigue su curso real independientemente de si el bloque
            # resultó corrupto: cada eslabón se ancla al anterior tal como fue
            # almacenado, no al que "debería" haber sido.
            previous_hash = registro.hash_actual

        if corruptos:
            estado_segmento = EstadoSegmento.CORRUPTA
        elif no_verificables:
            estado_segmento = EstadoSegmento.NO_CONCLUIDA
        else:
            estado_segmento = EstadoSegmento.INTEGRA
        return ResultadoVerificacionSegmento(
            device_id=device_id,
            desde=desde,
            hasta=hasta,
            integra=estado_segmento == EstadoSegmento.INTEGRA,
            total_bloques_verificados=fin - inicio + 1,
            registros_del_dispositivo=registros_dispositivo,
            primer_registro_inconsistente=primer_inconsistente,
            estado=estado_segmento,
            total_corruptos=corruptos,
            total_no_verificables=no_verificables,
            versiones_hash=tuple(sorted(versiones)),
            hash_ancla=ancla,
            alcance=alcance_para_versiones(versiones),
        )

    @staticmethod
    def _anclaje_del_sistema(
        registro: RegistroTrazabilidad, cadena: list[RegistroTrazabilidad], posicion: dict[str, int]
    ) -> bool:
        """HU-47: bloques que el sistema ancla fuera de la secuencia normal.

        - Génesis de aislamiento: previous_hash = génesis.
        - Aviso de corrupción: anclado al eslabón inmediatamente anterior al
          registro corrupto que cita (el último íntegro conocido).
        El sello de estos bloques se sigue comprobando aparte."""
        if registro.tipo_evento == EVENTO_REGISTRO_AISLADO:
            return registro.previous_hash == GENESIS_HASH
        if registro.tipo_evento == EVENTO_CORRUPCION_DETECTADA:
            i = posicion.get(str(registro.payload.get("registro_corrupto_id", "")))
            if i is None:
                return False
            anterior = cadena[i - 1].hash_actual if i > 0 else GENESIS_HASH
            return registro.previous_hash == anterior
        return False
