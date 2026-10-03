from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_device_repository import IDeviceRepository
from src.infrastructure.database.models import DeviceConfigHistoryModel, DeviceModel


def _to_dict(model: DeviceModel) -> dict:
    return {
        "id": model.id,
        "nombre": model.nombre,
        "ubicacion": model.ubicacion,
        "estado_conectividad": model.estado_conectividad,
        "created_at": model.created_at,
        "activo": model.activo,
        "firmware_version": model.firmware_version,
        "motivo_baja": model.motivo_baja,
        "descripcion_baja": model.descripcion_baja,
        "dado_de_baja_en": model.dado_de_baja_en,
        "reemplaza_a_device_id": model.reemplaza_a_device_id,
        "fecha_ultima_calibracion": model.fecha_ultima_calibracion,
        "numero_certificado_calibracion": model.numero_certificado_calibracion,
        "fecha_proxima_calibracion": model.fecha_proxima_calibracion,
        "observaciones_calibracion": model.observaciones_calibracion,
        "mqtt_token_activo": model.mqtt_token_activo,
        "mqtt_credencial_actualizada_en": model.mqtt_credencial_actualizada_en,
        "sensores_habilitados": (model.sensores_habilitados or {}).get("habilitados", []),
        "fecha_instalacion": model.fecha_instalacion,
        "instalado_por": model.instalado_por,
        "observaciones_instalacion": model.observaciones_instalacion,
        "responsable_nombre": model.responsable_nombre,
        "responsable_email": model.responsable_email,
        "responsable_telefono": model.responsable_telefono,
    }


class SQLAlchemyDeviceRepository(IDeviceRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def existe(self, device_id: str) -> bool:
        return await self._session.get(DeviceModel, device_id) is not None

    async def existio_alguna_vez(self, device_id: str) -> bool:
        return await self._session.get(DeviceModel, device_id) is not None

    async def crear(
        self,
        device_id: str,
        nombre: str | None,
        ubicacion: str | None,
        sensores_habilitados: list[str],
        admin_id: UUID,
    ) -> dict:
        model = DeviceModel(
            id=device_id,
            nombre=nombre,
            ubicacion=ubicacion,
            sensores_habilitados={"habilitados": sensores_habilitados},
            dado_de_alta_por=admin_id,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_dict(model)

    async def obtener_o_crear(self, device_id: str) -> dict:
        model = await self._session.get(DeviceModel, device_id)
        if model is None:
            model = DeviceModel(id=device_id)
            self._session.add(model)
            await self._session.flush()
            await self._session.refresh(model)
        return _to_dict(model)

    async def actualizar_estado_conectividad(self, device_id: str, estado: str) -> None:
        model = await self._session.get(DeviceModel, device_id)
        if model is None:
            model = DeviceModel(id=device_id, estado_conectividad=estado)
            self._session.add(model)
        else:
            model.estado_conectividad = estado
        await self._session.flush()

    async def listar(self) -> list[dict]:
        result = await self._session.execute(select(DeviceModel))
        return [_to_dict(m) for m in result.scalars().all()]

    async def obtener(self, device_id: str) -> dict | None:
        model = await self._session.get(DeviceModel, device_id)
        return _to_dict(model) if model else None

    async def dar_de_baja(
        self,
        device_id: str,
        motivo: str,
        descripcion: str | None,
        device_id_reemplazo: str | None,
        cuando: datetime,
    ) -> dict:
        model = await self._session.get(DeviceModel, device_id)
        if model is None:
            raise ValueError(f"Dispositivo {device_id} no encontrado")
        model.activo = False
        model.motivo_baja = motivo
        model.descripcion_baja = descripcion
        model.dado_de_baja_en = cuando
        if device_id_reemplazo:
            model.reemplaza_a_device_id = None  # el vínculo se registra en el dispositivo nuevo, no aquí
        await self._session.flush()
        return _to_dict(model)

    async def vincular_reemplazo(self, device_id_nuevo: str, device_id_anterior: str) -> None:
        model = await self._session.get(DeviceModel, device_id_nuevo)
        if model is None:
            model = DeviceModel(id=device_id_nuevo, reemplaza_a_device_id=device_id_anterior)
            self._session.add(model)
        else:
            model.reemplaza_a_device_id = device_id_anterior
        await self._session.flush()

    async def actualizar_firmware_version(self, device_id: str, version: str) -> None:
        model = await self._session.get(DeviceModel, device_id)
        if model is None:
            raise ValueError(f"Dispositivo {device_id} no encontrado")
        model.firmware_version = version
        await self._session.flush()

    # ── HU-30: calibración de sensores ────────────────────────────────────
    async def registrar_calibracion(
        self,
        device_id: str,
        fecha_calibracion: date,
        numero_certificado: str,
        fecha_proxima: date,
        observaciones: str | None,
    ) -> dict:
        model = await self._session.get(DeviceModel, device_id)
        if model is None:
            raise ValueError(f"Dispositivo {device_id} no encontrado")
        model.fecha_ultima_calibracion = fecha_calibracion
        model.numero_certificado_calibracion = numero_certificado
        model.fecha_proxima_calibracion = fecha_proxima
        model.observaciones_calibracion = observaciones
        await self._session.flush()
        return _to_dict(model)

    async def listar_calibracion_vencida(self, hoy: date) -> list[dict]:
        """Dispositivos activos cuyo certificado ya venció: sus lecturas dejan
        de ser evidencia válida hasta que se recalibren."""
        resultado = await self._session.execute(
            select(DeviceModel).where(
                DeviceModel.activo.is_(True),
                DeviceModel.fecha_proxima_calibracion.is_not(None),
                DeviceModel.fecha_proxima_calibracion < hoy,
            )
        )
        return [_to_dict(m) for m in resultado.scalars().all()]

    async def listar_calibracion_proxima(self, desde: date, hasta: date) -> list[dict]:
        resultado = await self._session.execute(
            select(DeviceModel).where(
                DeviceModel.activo.is_(True),
                DeviceModel.fecha_proxima_calibracion.is_not(None),
                DeviceModel.fecha_proxima_calibracion >= desde,
                DeviceModel.fecha_proxima_calibracion <= hasta,
            )
        )
        return [_to_dict(m) for m in resultado.scalars().all()]

    # ── HU-44/HU-10: credencial MQTT por dispositivo ───────────────────────
    async def guardar_credencial(self, device_id: str, token_hash: str, cuando: datetime) -> None:
        model = await self._session.get(DeviceModel, device_id)
        if model is None:
            raise ValueError(f"Dispositivo {device_id} no encontrado")
        model.mqtt_token_hash = token_hash
        model.mqtt_token_activo = True
        model.mqtt_credencial_actualizada_en = cuando
        await self._session.flush()

    async def revocar_credencial(self, device_id: str, cuando: datetime) -> None:
        model = await self._session.get(DeviceModel, device_id)
        if model is None:
            raise ValueError(f"Dispositivo {device_id} no encontrado")
        model.mqtt_token_activo = False
        model.mqtt_credencial_actualizada_en = cuando
        await self._session.flush()

    async def obtener_credencial(self, device_id: str) -> dict | None:
        model = await self._session.get(DeviceModel, device_id)
        if model is None:
            return None
        return {
            "mqtt_token_hash": model.mqtt_token_hash,
            "mqtt_token_activo": model.mqtt_token_activo,
        }

    # ── HU-51: ubicación y metadatos de instalación ────────────────────────
    async def actualizar_ubicacion_y_metadata(
        self,
        device_id: str,
        ubicacion: str | None,
        fecha_instalacion: date | None,
        instalado_por: UUID | None,
        observaciones: str | None,
    ) -> dict:
        model = await self._session.get(DeviceModel, device_id)
        if model is None:
            raise ValueError(f"Dispositivo {device_id} no encontrado")
        model.ubicacion = ubicacion
        model.fecha_instalacion = fecha_instalacion
        model.instalado_por = instalado_por
        model.observaciones_instalacion = observaciones
        await self._session.flush()
        return _to_dict(model)

    # ── HU-53/HU-54: responsable registrado (destinatario de notificaciones) ─
    async def actualizar_responsable(
        self,
        device_id: str,
        nombre: str | None,
        email: str | None,
        telefono: str | None,
    ) -> dict:
        model = await self._session.get(DeviceModel, device_id)
        if model is None:
            raise ValueError(f"Dispositivo {device_id} no encontrado")
        model.responsable_nombre = nombre
        model.responsable_email = email
        model.responsable_telefono = telefono
        await self._session.flush()
        return _to_dict(model)

    # ── HU-49: historial auditable de configuración ────────────────────────
    async def registrar_evento_config(
        self,
        device_id: str,
        campo: str,
        valor_anterior: str | None,
        valor_nuevo: str | None,
        actor_id: UUID | None,
    ) -> None:
        self._session.add(
            DeviceConfigHistoryModel(
                device_id=device_id,
                campo=campo,
                valor_anterior=valor_anterior,
                valor_nuevo=valor_nuevo,
                actor_id=actor_id,
            )
        )
        await self._session.flush()

    async def listar_historial_configuracion(self, device_id: str) -> list[dict]:
        stmt = (
            select(DeviceConfigHistoryModel)
            .where(DeviceConfigHistoryModel.device_id == device_id)
            .order_by(DeviceConfigHistoryModel.created_at.desc())
        )
        resultado = await self._session.execute(stmt)
        return [
            {
                "id": m.id,
                "device_id": m.device_id,
                "campo": m.campo,
                "valor_anterior": m.valor_anterior,
                "valor_nuevo": m.valor_nuevo,
                "actor_id": m.actor_id,
                "created_at": m.created_at,
            }
            for m in resultado.scalars().all()
        ]
