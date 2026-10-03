import secrets
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_device_repository import IDeviceRepository
from src.infrastructure.security.password_hasher import hash_password, verify_password

_MOTIVOS_BAJA = frozenset({"falla_hardware", "mantenimiento", "reemplazo", "fin_de_servicio"})


class DarDeBajaDispositivoUseCase:
    """HU-43: retira un ESP32/sensor de operación sin corromper la trazabilidad
    histórica — el dispositivo se marca inactivo, sus lecturas y alertas
    previas permanecen intactas, y (si aplica) el reemplazo hereda solo
    lectura del histórico mediante un vínculo explícito en `devices`."""

    def __init__(
        self,
        device_repository: IDeviceRepository,
        registrar_hash: RegistrarHashEncadenadoUseCase,
    ) -> None:
        self._device_repository = device_repository
        self._registrar_hash = registrar_hash

    async def execute(
        self,
        device_id: str,
        motivo: str,
        descripcion: str | None,
        device_id_reemplazo: str | None,
    ) -> dict:
        if motivo not in _MOTIVOS_BAJA:
            raise DomainError(f"Motivo de baja inválido: {motivo}")
        dispositivo = await self._device_repository.obtener(device_id)
        if dispositivo is None:
            raise RecursoNoEncontradoError(f"Dispositivo {device_id} no encontrado")
        if not dispositivo["activo"]:
            raise DomainError("El dispositivo ya está dado de baja")

        cuando = datetime.now(tz=timezone.utc)
        actualizado = await self._device_repository.dar_de_baja(
            device_id, motivo, descripcion, device_id_reemplazo, cuando
        )

        if device_id_reemplazo:
            # Cadena de custodia virtual: el nuevo device_id queda vinculado al
            # anterior para lectura de histórico; inicia su propia cadena hash
            # de lecturas independiente (no se copian ni migran registros).
            await self._device_repository.vincular_reemplazo(device_id_reemplazo, device_id)

        await self._registrar_hash.execute(
            tipo_evento="BAJA_HARDWARE",
            payload={
                "device_id_anterior": device_id,
                "motivo_baja": motivo,
                "device_id_reemplazo_si_existe": device_id_reemplazo,
                "timestamp_baja": cuando.isoformat(),
            },
            device_id=device_id,
        )
        return actualizado


def _sumar_meses(origen: date, meses: int) -> date:
    """Avanza `meses` sobre una fecha sin depender de dateutil. Si el día no
    existe en el mes destino (31 de enero + 1 mes), se ancla al último día
    válido — nunca desborda al mes siguiente."""
    total = origen.month - 1 + meses
    anio = origen.year + total // 12
    mes = total % 12 + 1
    dia_maximo = monthrange(anio, mes)[1]
    return date(anio, mes, min(origen.day, dia_maximo))


class RegistrarCalibracionUseCase:
    """HU-30: deja constancia del certificado de calibración de un sensor.

    Un registro térmico solo sirve como evidencia ante una inspección si el
    instrumento que lo produjo estaba calibrado. Por eso el certificado se
    ancla a la cadena SHA-256 (RF-14): quien audite el histórico puede
    demostrar que la calibración declarada no se alteró después.
    """

    def __init__(
        self,
        device_repository: IDeviceRepository,
        registrar_hash: RegistrarHashEncadenadoUseCase,
    ) -> None:
        self._device_repository = device_repository
        self._registrar_hash = registrar_hash

    async def execute(
        self,
        device_id: str,
        fecha_calibracion: date,
        numero_certificado: str,
        observaciones: str | None,
        meses_vigencia: int,
        usuario_id: UUID | None = None,
    ) -> dict:
        dispositivo = await self._device_repository.obtener(device_id)
        if dispositivo is None:
            raise RecursoNoEncontradoError(f"Dispositivo {device_id} no encontrado")
        if not dispositivo["activo"]:
            raise DomainError("No se puede calibrar un dispositivo dado de baja")

        fecha_proxima = _sumar_meses(fecha_calibracion, meses_vigencia)
        anterior = dispositivo.get("fecha_ultima_calibracion")
        actualizado = await self._device_repository.registrar_calibracion(
            device_id, fecha_calibracion, numero_certificado, fecha_proxima, observaciones
        )
        # HU-49: historial de configuración también cubre calibración.
        await self._device_repository.registrar_evento_config(
            device_id,
            campo="fecha_ultima_calibracion",
            valor_anterior=anterior.isoformat() if anterior else None,
            valor_nuevo=fecha_calibracion.isoformat(),
            actor_id=usuario_id,
        )

        await self._registrar_hash.execute(
            tipo_evento="CALIBRACION_SENSORES",
            payload={
                "device_id": device_id,
                "fecha_calibracion": fecha_calibracion.isoformat(),
                "numero_certificado": numero_certificado,
                "fecha_proxima_calibracion": fecha_proxima.isoformat(),
                "meses_vigencia": meses_vigencia,
                "observaciones": observaciones,
            },
            device_id=device_id,
            usuario_id=usuario_id,
        )
        return actualizado


class ConsultarEstadoCalibracionUseCase:
    """Dispositivos con certificado vencido o por vencer, para avisar antes de
    que el histórico térmico pierda validez documental."""

    def __init__(self, device_repository: IDeviceRepository) -> None:
        self._device_repository = device_repository

    async def execute(self, hoy: date, dias_preaviso: int = 30) -> dict:
        vencidos = await self._device_repository.listar_calibracion_vencida(hoy)
        proximos = await self._device_repository.listar_calibracion_proxima(
            hoy, hoy + timedelta(days=dias_preaviso)
        )
        return {"vencidos": vencidos, "proximos_a_vencer": proximos}


class RotarCredencialDispositivoUseCase:
    """HU-44/HU-10: genera un nuevo token MQTT para un dispositivo y lo deja
    activo, invalidando implícitamente cualquier token anterior (se
    sobrescribe el hash, no se conservan hashes previos como válidos). El
    valor en claro se devuelve UNA sola vez para aprovisionarlo en el ESP32
    — igual que una API key, el backend nunca vuelve a poder mostrarlo
    porque solo conserva su hash."""

    def __init__(
        self, device_repository: IDeviceRepository, registrar_hash: RegistrarHashEncadenadoUseCase
    ) -> None:
        self._device_repository = device_repository
        self._registrar_hash = registrar_hash

    async def execute(self, device_id: str, admin_id: UUID, motivo: str) -> str:
        dispositivo = await self._device_repository.obtener(device_id)
        if dispositivo is None:
            raise RecursoNoEncontradoError(f"Dispositivo {device_id} no encontrado")
        if not dispositivo["activo"]:
            raise DomainError("No se puede aprovisionar credenciales a un dispositivo dado de baja")

        token = secrets.token_urlsafe(32)
        cuando = datetime.now(tz=timezone.utc)
        await self._device_repository.guardar_credencial(device_id, hash_password(token), cuando)

        await self._registrar_hash.execute(
            tipo_evento="ROTACION_CREDENCIAL_DISPOSITIVO",
            payload={"device_id": device_id, "admin_id": str(admin_id), "motivo": motivo},
            device_id=device_id,
            usuario_id=admin_id,
            timestamp=cuando,
        )
        return token


class RevocarCredencialDispositivoUseCase:
    """HU-44: corta de inmediato el acceso de un nodo cuyas credenciales se
    sospechan comprometidas, sin alterar su historial de telemetría."""

    def __init__(
        self, device_repository: IDeviceRepository, registrar_hash: RegistrarHashEncadenadoUseCase
    ) -> None:
        self._device_repository = device_repository
        self._registrar_hash = registrar_hash

    async def execute(self, device_id: str, admin_id: UUID, motivo: str) -> None:
        dispositivo = await self._device_repository.obtener(device_id)
        if dispositivo is None:
            raise RecursoNoEncontradoError(f"Dispositivo {device_id} no encontrado")

        cuando = datetime.now(tz=timezone.utc)
        await self._device_repository.revocar_credencial(device_id, cuando)

        await self._registrar_hash.execute(
            tipo_evento="REVOCACION_CREDENCIAL_DISPOSITIVO",
            payload={"device_id": device_id, "admin_id": str(admin_id), "motivo": motivo},
            device_id=device_id,
            usuario_id=admin_id,
            timestamp=cuando,
        )


class VerificarCredencialDispositivoUseCase:
    """Punto de integración con el broker: EMQX Cloud puede configurarse con
    un backend de autenticación HTTP que llame a este caso de uso (vía el
    endpoint no autenticado /api/dispositivos/autenticar) para decidir si
    acepta la conexión de un ESP32. Un token revocado o nunca aprovisionado
    se rechaza; el reason code MQTT 5.0 exacto (0x87) lo aplica EMQX según
    la respuesta HTTP, no este caso de uso."""

    def __init__(self, device_repository: IDeviceRepository) -> None:
        self._device_repository = device_repository

    async def execute(self, device_id: str, token: str) -> bool:
        credencial = await self._device_repository.obtener_credencial(device_id)
        if credencial is None or not credencial["mqtt_token_activo"]:
            return False
        if credencial["mqtt_token_hash"] is None:
            return False
        return verify_password(token, credencial["mqtt_token_hash"])


class RegistrarAltaDispositivoUseCase:
    """HU-43 Escenario 1: alta explícita de un nuevo nodo — un device_id
    único, estado activo, sensores habilitados y metadatos básicos, sin
    reutilizar la identidad de un equipo que haya existido antes (incluso
    dado de baja)."""

    def __init__(
        self, device_repository: IDeviceRepository, registrar_hash: RegistrarHashEncadenadoUseCase
    ) -> None:
        self._device_repository = device_repository
        self._registrar_hash = registrar_hash

    async def execute(
        self,
        device_id: str,
        nombre: str | None,
        ubicacion: str | None,
        sensores_habilitados: list[str],
        admin_id: UUID,
    ) -> dict:
        if await self._device_repository.existio_alguna_vez(device_id):
            raise DomainError(
                f"El device_id {device_id} ya fue usado por otro equipo (activo o dado de baja)"
            )

        creado = await self._device_repository.crear(
            device_id, nombre, ubicacion, sensores_habilitados, admin_id
        )

        await self._registrar_hash.execute(
            tipo_evento="ALTA_DISPOSITIVO",
            payload={
                "device_id": device_id,
                "nombre": nombre,
                "ubicacion": ubicacion,
                "sensores_habilitados": sensores_habilitados,
                "admin_id": str(admin_id),
            },
            device_id=device_id,
            usuario_id=admin_id,
        )
        return creado


class ActualizarUbicacionYMetadatosInstalacionUseCase:
    """HU-51: documenta la ubicación física del DS18B20 y los metadatos de
    instalación. Un cambio de ubicación genera un evento histórico (HU-49)
    en vez de sobrescribir en silencio la ubicación previa."""

    def __init__(
        self, device_repository: IDeviceRepository, registrar_hash: RegistrarHashEncadenadoUseCase
    ) -> None:
        self._device_repository = device_repository
        self._registrar_hash = registrar_hash

    async def execute(
        self,
        device_id: str,
        ubicacion: str | None,
        fecha_instalacion: date | None,
        instalado_por: UUID,
        observaciones: str | None,
    ) -> dict:
        anterior = await self._device_repository.obtener(device_id)
        if anterior is None:
            raise RecursoNoEncontradoError(f"Dispositivo {device_id} no encontrado")

        actualizado = await self._device_repository.actualizar_ubicacion_y_metadata(
            device_id, ubicacion, fecha_instalacion, instalado_por, observaciones
        )

        if anterior["ubicacion"] != ubicacion:
            await self._device_repository.registrar_evento_config(
                device_id,
                campo="ubicacion",
                valor_anterior=anterior["ubicacion"],
                valor_nuevo=ubicacion,
                actor_id=instalado_por,
            )

        await self._registrar_hash.execute(
            tipo_evento="INSTALACION_DISPOSITIVO_ACTUALIZADA",
            payload={
                "device_id": device_id,
                "ubicacion_anterior": anterior["ubicacion"],
                "ubicacion_nueva": ubicacion,
                "fecha_instalacion": fecha_instalacion.isoformat() if fecha_instalacion else None,
                "instalado_por": str(instalado_por),
                "observaciones": observaciones,
            },
            device_id=device_id,
            usuario_id=instalado_por,
        )
        return actualizado


class ActualizarResponsableDispositivoUseCase:
    """HU-53/HU-54: registra quién recibe los avisos de excursión crítica de
    este dispositivo por correo y SMS — reemplaza el destinatario único
    global (SMTP_TO) por uno propio de cada unidad monitoreada."""

    def __init__(
        self, device_repository: IDeviceRepository, registrar_hash: RegistrarHashEncadenadoUseCase
    ) -> None:
        self._device_repository = device_repository
        self._registrar_hash = registrar_hash

    async def execute(
        self,
        device_id: str,
        nombre: str | None,
        email: str | None,
        telefono: str | None,
        actor_id: UUID,
    ) -> dict:
        anterior = await self._device_repository.obtener(device_id)
        if anterior is None:
            raise RecursoNoEncontradoError(f"Dispositivo {device_id} no encontrado")

        actualizado = await self._device_repository.actualizar_responsable(
            device_id, nombre, email, telefono
        )
        if anterior["responsable_email"] != email or anterior["responsable_telefono"] != telefono:
            await self._device_repository.registrar_evento_config(
                device_id,
                campo="responsable_contacto",
                valor_anterior=f"{anterior['responsable_email']} / {anterior['responsable_telefono']}",
                valor_nuevo=f"{email} / {telefono}",
                actor_id=actor_id,
            )

        await self._registrar_hash.execute(
            tipo_evento="RESPONSABLE_DISPOSITIVO_ACTUALIZADO",
            payload={
                "device_id": device_id,
                "responsable_nombre": nombre,
                # El correo/teléfono del responsable SÍ queda en el payload:
                # es el dato que la auditoría necesita para confirmar a quién
                # se avisó ante una excursión, igual que el resto de metadatos
                # de contacto operativo que ya viajan en claro en esta cadena.
                "responsable_email": email,
                "responsable_telefono": telefono,
                "actor_id": str(actor_id),
            },
            device_id=device_id,
            usuario_id=actor_id,
        )
        return actualizado


class ConsultarHistorialConfiguracionUseCase:
    """HU-49: historial de cambios de configuración/instalación/calibración/
    reemplazo de un dispositivo, de solo lectura."""

    def __init__(self, device_repository: IDeviceRepository) -> None:
        self._device_repository = device_repository

    async def execute(self, device_id: str) -> list[dict]:
        return await self._device_repository.listar_historial_configuracion(device_id)
