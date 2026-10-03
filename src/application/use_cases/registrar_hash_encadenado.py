import asyncio
from datetime import datetime, timezone
from uuid import UUID

from src.domain.entities.registro_trazabilidad import RegistroTrazabilidad
from src.domain.repositories.i_trazabilidad_repository import ITrazabilidadRepository
from src.domain.value_objects.hash_encadenado import (
    HASH_FORMULA_VERSION_ACTUAL,
    HashEncadenado,
    timestamp_canonico,
)

# HU-25: cadena independiente por unidad monitoreada. Un evento ligado a un
# dispositivo (lectura, alerta, acción correctiva, evento de conectividad)
# encadena bajo chain_id=device_id; un evento sin dispositivo (login, cambio
# de rol, checklist, exportación, gobierno de modelo IA) encadena bajo esta
# cadena de sistema — nunca se mezclan eventos de unidades monitoreadas
# distintas entre sí (criterio de HU-25), y las cadenas de sistema y de
# dispositivo tampoco se mezclan entre sí.
CHAIN_ID_SISTEMA = "SISTEMA"

# Concurrencia: leer el último hash e insertar el siguiente eslabón es una
# sección crítica de lectura-luego-escritura. Sin serializarla, dos escrituras
# casi simultáneas de la MISMA cadena pueden leer el mismo previous_hash y
# bifurcarla (hallazgo B-01 de la auditoría). Este backend es de un solo
# proceso por diseño (mismo supuesto ya documentado en JtiStore y
# SlidingWindowRateLimiter), así que un candado a nivel de proceso serializa
# correctamente todas las escrituras sin depender del dialecto de base de
# datos (Postgres o SQLite en pruebas). En un despliegue multi-worker real,
# este candado NO basta (cada worker tiene su propio proceso) y se refuerza
# con un bloqueo a nivel de base de datos derivado de chain_id (ver
# obtener_ultimo_eslabon() en trazabilidad_repository.py) — limitación
# conocida y documentada, igual que las demás estructuras en memoria de este
# prototipo. El candado de proceso es único (no por chain_id): serializa algo
# más de lo estrictamente necesario entre cadenas distintas, pero eso solo
# afecta throughput, nunca corrección.
class _CandadoDeProceso:
    """asyncio.Lock que se rebina automáticamente si cambia el event loop en
    ejecución. Un asyncio.Lock ordinario creado a nivel de módulo queda atado
    al primer loop que lo usa; en un proceso de producción real solo existe un
    loop durante toda la vida del proceso, así que esto nunca ocurre. Pero en
    la suite de pruebas, pytest-asyncio crea un loop nuevo por test, y un lock
    puramente módulo-global fallaría con 'bound to a different event loop' al
    segundo test que lo usara. Este wrapper lo hace seguro en ambos casos."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _lock_actual(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock

    async def __aenter__(self) -> None:
        await self._lock_actual().acquire()

    async def __aexit__(self, *_exc_info: object) -> None:
        self._lock_actual().release()


_CANDADO_CADENA = _CandadoDeProceso()


class RegistrarHashEncadenadoUseCase:
    """Genera un nuevo eslabón de la cadena SHA-256 para cualquier evento auditable
    (RF-14: lectura, alerta, acción correctiva, reporte, auditoría, conectividad).

    La lectura del último hash y la inserción del nuevo registro se ejecutan
    dentro de una sección crítica serializada (_CANDADO_CADENA) para impedir
    que dos eventos concurrentes bifurquen la cadena (ver comentario del módulo).
    """

    def __init__(self, trazabilidad_repository: ITrazabilidadRepository) -> None:
        self._trazabilidad_repository = trazabilidad_repository

    async def execute(
        self,
        tipo_evento: str,
        payload: dict,
        device_id: str | None = None,
        usuario_id: UUID | None = None,
        timestamp: datetime | None = None,
        previous_hash_forzado: str | None = None,
        chain_id: str | None = None,
    ) -> RegistroTrazabilidad:
        """previous_hash_forzado (HU-47): permite anclar un evento de emergencia
        al último bloque ÍNTEGRO conocido de la cadena (en vez de al último hash
        almacenado, que puede ya ser descendiente de un registro corrupto).

        chain_id (HU-25): cadena explícita del evento. Si se omite, se deriva
        de device_id (una cadena por unidad monitoreada) o, si el evento no
        está ligado a un dispositivo, de CHAIN_ID_SISTEMA."""
        timestamp = timestamp or datetime.now(tz=timezone.utc)
        chain_id_efectivo = chain_id or device_id or CHAIN_ID_SISTEMA

        async def registrar() -> RegistroTrazabilidad:
            ultimo_hash, ultimo_seq = await self._trazabilidad_repository.obtener_ultimo_eslabon(
                chain_id_efectivo
            )
            previous_hash = previous_hash_forzado or ultimo_hash
            hash_encadenado = HashEncadenado.encadenar(
                chain_id_efectivo, previous_hash, timestamp_canonico(timestamp), payload
            )
            registro = RegistroTrazabilidad(
                tipo_evento=tipo_evento,
                payload=payload,
                timestamp=timestamp,
                hash_encadenado=hash_encadenado,
                chain_id=chain_id_efectivo,
                chain_seq=ultimo_seq + 1,
                hash_version=HASH_FORMULA_VERSION_ACTUAL,
                device_id=device_id,
                usuario_id=usuario_id,
            )
            return await self._trazabilidad_repository.agregar(registro)

        # PostgreSQL mantiene pg_advisory_xact_lock hasta commit. Combinarlo
        # con lock de proceso causa inversión: sesión A necesita lock proceso
        # para su segundo hash mientras B lo retiene esperando lock PostgreSQL
        # de A. SQLite no tiene advisory lock, por eso conserva este candado.
        session = getattr(self._trazabilidad_repository, "_session", None)
        bind = getattr(session, "bind", None)
        if bind is not None and bind.dialect.name == "postgresql":
            return await registrar()
        async with _CANDADO_CADENA:
            return await registrar()
