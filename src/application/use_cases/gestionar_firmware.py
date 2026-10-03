from datetime import datetime, timezone

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_device_repository import IDeviceRepository
from src.domain.repositories.i_firmware_repository import IFirmwareRepository

# HU-46: OTA simulado a nivel de metadata/API. Este repositorio no contiene
# firmware real de ESP32 (no hay .bin, no hay cifrado AES-256 de un binario,
# no hay canal MQTT de producción con chunks). Lo que sí se implementa y
# verifica de verdad: versionado semántico, la regla de "nunca permitir
# downgrade" (Escenario 2 y 6), el registro encadenado de cada evento, y la
# máquina de estados programado -> en_progreso -> exitoso/fallido/rollback.


def _version_tupla(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError as exc:
        raise DomainError(f"Versión de firmware inválida: {version}") from exc


class PrepararFirmwareUseCase:
    def __init__(self, firmware_repository: IFirmwareRepository, registrar_hash: RegistrarHashEncadenadoUseCase) -> None:
        self._firmware_repository = firmware_repository
        self._registrar_hash = registrar_hash

    async def execute(self, version: str, hash_sha256: str, descripcion: str) -> dict:
        _version_tupla(version)
        if await self._firmware_repository.obtener_release(version) is not None:
            raise DomainError(f"Ya existe una release con versión {version}")
        fecha_compilacion = datetime.now(tz=timezone.utc)
        release = await self._firmware_repository.crear_release(version, hash_sha256, descripcion, fecha_compilacion)
        await self._registrar_hash.execute(
            tipo_evento="FIRMWARE_PREPARACION",
            payload={
                "version_firmware": version,
                "hash_sha256": hash_sha256,
                "fecha_compilacion": fecha_compilacion.isoformat(),
                "descripcion_parche": descripcion,
            },
        )
        return release


class ProgramarDespliegueUseCase:
    """HU-46 Escenario 2: rechaza downgrades ANTES de programar el despliegue."""

    def __init__(self, device_repository: IDeviceRepository, firmware_repository: IFirmwareRepository) -> None:
        self._device_repository = device_repository
        self._firmware_repository = firmware_repository

    async def execute(self, device_id: str, version_objetivo: str, programado_para: datetime | None) -> dict:
        dispositivo = await self._device_repository.obtener(device_id)
        if dispositivo is None:
            raise RecursoNoEncontradoError(f"Dispositivo {device_id} no encontrado")
        release = await self._firmware_repository.obtener_release(version_objetivo)
        if release is None:
            raise RecursoNoEncontradoError(f"No existe una release preparada con versión {version_objetivo}")
        if _version_tupla(version_objetivo) <= _version_tupla(dispositivo["firmware_version"]):
            raise DomainError(
                f"Downgrade rechazado: {device_id} ya tiene {dispositivo['firmware_version']}, "
                f"no se permite desplegar {version_objetivo}"
            )
        return await self._firmware_repository.crear_despliegue(device_id, version_objetivo, programado_para)


class EjecutarDespliegueUseCase:
    """Simula la ventana de despliegue (Escenarios 3-5): sin ESP32 real no hay
    fragmentación/cifrado que verificar, así que el resultado se determina por
    la validación de versión (éxito) o por un downgrade detectado tardíamente
    (fallo + evento de ataque, Escenario 6) — no por una tirada aleatoria."""

    def __init__(
        self,
        device_repository: IDeviceRepository,
        firmware_repository: IFirmwareRepository,
        registrar_hash: RegistrarHashEncadenadoUseCase,
    ) -> None:
        self._device_repository = device_repository
        self._firmware_repository = firmware_repository
        self._registrar_hash = registrar_hash

    async def execute(self, despliegue_id) -> dict:
        despliegue = await self._firmware_repository.obtener_despliegue(despliegue_id)
        if despliegue is None:
            raise RecursoNoEncontradoError(f"Despliegue {despliegue_id} no encontrado")
        if despliegue["estado"] != "programado":
            raise DomainError(f"El despliegue ya fue procesado (estado={despliegue['estado']})")

        dispositivo = await self._device_repository.obtener(despliegue["device_id"])
        if dispositivo is None:
            raise RecursoNoEncontradoError(f"Dispositivo {despliegue['device_id']} no encontrado")

        version_actual = dispositivo["firmware_version"]
        version_objetivo = despliegue["version_objetivo"]
        ahora = datetime.now(tz=timezone.utc)

        if _version_tupla(version_objetivo) <= _version_tupla(version_actual):
            # Ataque de downgrade detectado en el momento de ejecutar (p. ej. el
            # dispositivo fue actualizado por otra vía entre la programación y
            # la ejecución) — se rechaza y se deja constancia, no se aplica.
            actualizado = await self._firmware_repository.actualizar_despliegue(
                despliegue_id, "fallido", "downgrade_rechazado_en_ejecucion", ahora
            )
            await self._registrar_hash.execute(
                tipo_evento="FIRMWARE_ROLLBACK",
                payload={
                    "device_id": despliegue["device_id"],
                    "version_anterior": version_actual,
                    "version_nueva": version_objetivo,
                    "resultado": "fallo_downgrade_rechazado",
                },
                device_id=despliegue["device_id"],
            )
            return actualizado

        await self._device_repository.actualizar_firmware_version(despliegue["device_id"], version_objetivo)
        actualizado = await self._firmware_repository.actualizar_despliegue(
            despliegue_id, "exitoso", "firmware_instalado_y_autotests_ok", ahora
        )
        await self._registrar_hash.execute(
            tipo_evento="FIRMWARE_ACTUALIZADO",
            payload={
                "device_id": despliegue["device_id"],
                "version_anterior": version_actual,
                "version_nueva": version_objetivo,
                "resultado": "exito",
            },
            device_id=despliegue["device_id"],
        )
        return actualizado
