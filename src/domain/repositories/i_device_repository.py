from abc import ABC, abstractmethod
from datetime import date, datetime
from uuid import UUID


class IDeviceRepository(ABC):
    @abstractmethod
    async def existe(self, device_id: str) -> bool: ...

    @abstractmethod
    async def existio_alguna_vez(self, device_id: str) -> bool:
        """HU-43 criterio 1: incluye dispositivos dados de baja — un
        device_id nunca se reutiliza, ni siquiera tras retirarlo."""
        ...

    @abstractmethod
    async def crear(
        self,
        device_id: str,
        nombre: str | None,
        ubicacion: str | None,
        sensores_habilitados: list[str],
        admin_id: UUID,
    ) -> dict: ...

    @abstractmethod
    async def obtener_o_crear(self, device_id: str) -> dict: ...

    @abstractmethod
    async def actualizar_estado_conectividad(self, device_id: str, estado: str) -> None: ...

    @abstractmethod
    async def listar(self) -> list[dict]: ...

    @abstractmethod
    async def obtener(self, device_id: str) -> dict | None: ...

    @abstractmethod
    async def dar_de_baja(
        self,
        device_id: str,
        motivo: str,
        descripcion: str | None,
        device_id_reemplazo: str | None,
        cuando: datetime,
    ) -> dict: ...

    @abstractmethod
    async def vincular_reemplazo(self, device_id_nuevo: str, device_id_anterior: str) -> None: ...

    @abstractmethod
    async def actualizar_firmware_version(self, device_id: str, version: str) -> None: ...

    # ── HU-30: calibración de sensores ────────────────────────────────────
    @abstractmethod
    async def registrar_calibracion(
        self,
        device_id: str,
        fecha_calibracion: date,
        numero_certificado: str,
        fecha_proxima: date,
        observaciones: str | None,
    ) -> dict: ...

    @abstractmethod
    async def listar_calibracion_vencida(self, hoy: date) -> list[dict]: ...

    @abstractmethod
    async def listar_calibracion_proxima(self, desde: date, hasta: date) -> list[dict]: ...

    # ── HU-44/HU-10: credencial MQTT por dispositivo ───────────────────────
    @abstractmethod
    async def guardar_credencial(self, device_id: str, token_hash: str, cuando: datetime) -> None: ...

    @abstractmethod
    async def revocar_credencial(self, device_id: str, cuando: datetime) -> None: ...

    @abstractmethod
    async def obtener_credencial(self, device_id: str) -> dict | None:
        """None si el dispositivo no existe. Si existe, incluye
        mqtt_token_hash (puede ser None si nunca se aprovisionó) y
        mqtt_token_activo."""
        ...

    # ── HU-51: ubicación y metadatos de instalación ────────────────────────
    @abstractmethod
    async def actualizar_ubicacion_y_metadata(
        self,
        device_id: str,
        ubicacion: str | None,
        fecha_instalacion: date | None,
        instalado_por: UUID | None,
        observaciones: str | None,
    ) -> dict: ...

    # ── HU-53/HU-54: responsable registrado (destinatario de notificaciones) ─
    @abstractmethod
    async def actualizar_responsable(
        self,
        device_id: str,
        nombre: str | None,
        email: str | None,
        telefono: str | None,
    ) -> dict: ...

    # ── HU-49: historial auditable de configuración ────────────────────────
    @abstractmethod
    async def registrar_evento_config(
        self,
        device_id: str,
        campo: str,
        valor_anterior: str | None,
        valor_nuevo: str | None,
        actor_id: UUID | None,
    ) -> None: ...

    @abstractmethod
    async def listar_historial_configuracion(self, device_id: str) -> list[dict]: ...
