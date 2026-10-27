from abc import ABC, abstractmethod
from datetime import date, datetime
from uuid import UUID


class IDeviceRepository(ABC):
    @abstractmethod
    async def existe(self, device_id: str) -> bool: ...

    @abstractmethod
    async def esta_activo(self, device_id: str) -> bool:
        """BE-003: True solo si el dispositivo existe y no fue dado de baja."""

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
        es_demo: bool = False,
    ) -> dict: ...

    @abstractmethod
    async def obtener_o_crear(self, device_id: str) -> dict: ...

    @abstractmethod
    async def actualizar_estado_conectividad(
        self,
        device_id: str,
        estado: str,
        boot_id: int | None = None,
        momento: datetime | None = None,
    ) -> None: ...

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
    async def guardar_credencial(
        self, device_id: str, token_hash: str, cuando: datetime, pop_clave: str | None = None
    ) -> None: ...

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

    # ── HU-43/HU-44: operación pendiente en el broker ─────────────────────
    @abstractmethod
    async def marcar_pendiente_broker(
        self, device_id: str, operacion: str, motivo: str, actor_id: UUID | None, cuando: datetime
    ) -> None:
        """Deja constancia de que el usuario del nodo en el broker falta por
        `aprovisionar` o `retirar` (consola de EMQX o fallo de su API)."""

    @abstractmethod
    async def limpiar_pendiente_broker(self, device_id: str) -> None:
        """La API del broker completó la operación: ya no hay nada pendiente."""

    @abstractmethod
    async def marcar_rotacion_por_compromiso(self, device_id: str) -> None:
        """La rotación pendiente es por token filtrado: la ingesta retiene la
        telemetría del nodo hasta que, con la consola confirmada, el nodo pruebe
        que tiene el token nuevo (`concluir_rotacion_por_compromiso`)."""

    @abstractmethod
    async def registrar_desafio_posesion(self, device_id: str, nonce: str, cuando: datetime) -> None:
        """HU-44: nonce vigente del desafío de posesión (sustituye al anterior)."""

    @abstractmethod
    async def concluir_rotacion_por_compromiso(self, device_id: str) -> None:
        """HU-44: el nodo probó que tiene el token nuevo: termina la retención."""

    @abstractmethod
    async def confirmar_broker(self, device_id: str, actor_id: UUID, cuando: datetime) -> dict:
        """El administrador confirma que hizo en la consola la operación pendiente."""

    # ── HU-54: intervalo de captura y latido del nodo ──────────────────────
    @abstractmethod
    async def actualizar_intervalo_reporte(self, device_id: str, segundos: int) -> dict: ...

    @abstractmethod
    async def registrar_latido(self, device_id: str, cuando: datetime) -> None: ...
