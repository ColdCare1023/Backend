import secrets
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.exceptions import DomainError, RecursoNoEncontradoError
from src.domain.repositories.i_device_repository import IDeviceRepository
from src.infrastructure.security.password_hasher import hash_password_async
from src.infrastructure.security.privacidad import enmascarar_email


def _contacto_minimizado(email: str | None, telefono: str | None) -> str:
    """B-10 (Ley 29733): la cadena y el historial son inmutables; guardan el
    contacto enmascarado, suficiente para saber a quién se avisaba."""
    correo = enmascarar_email(email) if email else None
    fono = f"***{telefono[-3:]}" if telefono else None
    return f"{correo} / {fono}"

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
        if device_id_reemplazo is not None:
            # BE-017: el reemplazo es un dispositivo ya dado de alta (HU-43), no
            # uno que la baja cree por la puerta de atrás sin pasar por el alta.
            if device_id_reemplazo == device_id:
                raise DomainError("Un dispositivo no puede reemplazarse a sí mismo")
            if not await self._device_repository.esta_activo(device_id_reemplazo):
                raise DomainError(
                    f"El dispositivo de reemplazo {device_id_reemplazo} debe estar dado de alta y activo"
                )

        cuando = datetime.now(tz=timezone.utc)
        actualizado = await self._device_repository.dar_de_baja(
            device_id, motivo, descripcion, device_id_reemplazo, cuando
        )
        # BE-003: un equipo retirado (o extraviado) no conserva una credencial
        # válida. La sesión abierta la expulsa el router tras el COMMIT.
        await self._device_repository.revocar_credencial(device_id, cuando)
        actualizado["mqtt_token_activo"] = False

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
    porque solo conserva su hash. El llamador lo aprovisiona además en la
    autenticación integrada del broker (BrokerAdmin.aprovisionar_credencial)."""

    def __init__(
        self, device_repository: IDeviceRepository, registrar_hash: RegistrarHashEncadenadoUseCase
    ) -> None:
        self._device_repository = device_repository
        self._registrar_hash = registrar_hash

    async def execute(
        self,
        device_id: str,
        admin_id: UUID,
        motivo: str,
        tipo_evento: str = "ROTACION_CREDENCIAL_DISPOSITIVO",
        causa: str = "preventiva",
    ) -> str:
        dispositivo = await self._device_repository.obtener(device_id)
        if dispositivo is None:
            raise RecursoNoEncontradoError(f"Dispositivo {device_id} no encontrado")
        if not dispositivo["activo"]:
            raise DomainError("No se puede aprovisionar credenciales a un dispositivo dado de baja")

        token = secrets.token_urlsafe(32)
        cuando = datetime.now(tz=timezone.utc)
        await self._device_repository.guardar_credencial(device_id, await hash_password_async(token), cuando)

        await self._registrar_hash.execute(
            tipo_evento=tipo_evento,
            payload={"device_id": device_id, "admin_id": str(admin_id), "motivo": motivo, "causa": causa},
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
        es_demo: bool = False,
    ) -> dict:
        if await self._device_repository.existio_alguna_vez(device_id):
            raise DomainError(
                f"El device_id {device_id} ya fue usado por otro equipo (activo o dado de baja)"
            )

        creado = await self._device_repository.crear(
            device_id, nombre, ubicacion, sensores_habilitados, admin_id, es_demo=es_demo
        )

        await self._registrar_hash.execute(
            tipo_evento="ALTA_DISPOSITIVO",
            payload={
                "device_id": device_id,
                "nombre": nombre,
                "ubicacion": ubicacion,
                "sensores_habilitados": sensores_habilitados,
                "admin_id": str(admin_id),
                "es_demo": es_demo,
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
                valor_anterior=_contacto_minimizado(anterior["responsable_email"], anterior["responsable_telefono"]),
                valor_nuevo=_contacto_minimizado(email, telefono),
                actor_id=actor_id,
            )

        await self._registrar_hash.execute(
            tipo_evento="RESPONSABLE_DISPOSITIVO_ACTUALIZADO",
            payload={
                "device_id": device_id,
                "responsable_nombre": nombre,
                # B-10 (Ley 29733): la cadena es inmutable, así que guarda el
                # contacto minimizado (inicial + dominio, últimos 3 dígitos):
                # basta para confirmar a quién se avisó. El valor completo vive
                # solo en `devices`, donde sí se puede rectificar o suprimir.
                "responsable_email": enmascarar_email(email) if email else None,
                "responsable_telefono": f"***{telefono[-3:]}" if telefono else None,
                "actor_id": str(actor_id),
            },
            device_id=device_id,
            usuario_id=actor_id,
        )
        return actualizado


# HU-43/HU-44: operaciones sobre el usuario del nodo en el broker.
OPERACION_BROKER_APROVISIONAR = "aprovisionar"
OPERACION_BROKER_RETIRAR = "retirar"


class RegistrarResultadoBrokerUseCase:
    """HU-44 Escenario 3: el backend no informa éxito en silencio. Si el broker
    no confirmó la operación (modo manual de EMQX Serverless, API sin
    configurar o con error), el dispositivo queda con la operación pendiente
    hasta que un administrador confirme que la hizo en la consola."""

    def __init__(
        self, device_repository: IDeviceRepository, registrar_hash: RegistrarHashEncadenadoUseCase
    ) -> None:
        self._device_repository = device_repository
        self._registrar_hash = registrar_hash

    async def execute(
        self, device_id: str, operacion: str, resultado: bool | None, motivo: str, actor_id: UUID | None
    ) -> bool:
        """Devuelve True si la operación quedó pendiente."""
        if resultado is True:
            dispositivo = await self._device_repository.obtener(device_id)
            if dispositivo is not None and dispositivo.get("broker_pendiente") == operacion:
                await self._device_repository.limpiar_pendiente_broker(device_id)
            return False
        cuando = datetime.now(tz=timezone.utc)
        await self._device_repository.marcar_pendiente_broker(device_id, operacion, motivo, actor_id, cuando)
        await self._registrar_hash.execute(
            tipo_evento="BROKER_OPERACION_PENDIENTE",
            payload={
                "device_id": device_id,
                "operacion": operacion,
                "motivo": motivo,
                "actor_id": str(actor_id) if actor_id else None,
            },
            device_id=device_id,
            usuario_id=actor_id,
            timestamp=cuando,
        )
        return True


class ConfirmarOperacionBrokerUseCase:
    """HU-43/HU-44: el administrador declara que ya creó, actualizó o retiró el
    usuario del nodo en la consola del broker. Queda en la cadena quién, cuándo
    y qué operación cerró; sin operación pendiente no hay nada que confirmar."""

    def __init__(
        self, device_repository: IDeviceRepository, registrar_hash: RegistrarHashEncadenadoUseCase
    ) -> None:
        self._device_repository = device_repository
        self._registrar_hash = registrar_hash

    async def execute(self, device_id: str, admin_id: UUID, nota: str | None) -> tuple[dict, str]:
        dispositivo = await self._device_repository.obtener(device_id)
        if dispositivo is None:
            raise RecursoNoEncontradoError(f"Dispositivo {device_id} no encontrado")
        operacion = dispositivo.get("broker_pendiente")
        if not operacion:
            raise DomainError("El dispositivo no tiene ninguna operación pendiente en el broker")
        pendiente_desde = dispositivo.get("broker_pendiente_desde")
        cuando = datetime.now(tz=timezone.utc)
        actualizado = await self._device_repository.confirmar_broker(device_id, admin_id, cuando)
        await self._registrar_hash.execute(
            tipo_evento="BROKER_CONSOLA_CONFIRMADA",
            payload={
                "device_id": device_id,
                "operacion": operacion,
                "pendiente_desde": pendiente_desde.isoformat() if pendiente_desde else None,
                "admin_id": str(admin_id),
                "nota": nota,
            },
            device_id=device_id,
            usuario_id=admin_id,
            timestamp=cuando,
        )
        return actualizado, operacion


class ActualizarIntervaloReporteUseCase:
    """HU-54: intervalo de captura del nodo. El umbral SIN_DATOS es N veces
    este valor, así que no puede quedar escrito en el código. Un cambio queda
    en el historial de configuración (HU-49)."""

    MINIMO_SEGUNDOS = 5
    MAXIMO_SEGUNDOS = 3600

    def __init__(self, device_repository: IDeviceRepository) -> None:
        self._device_repository = device_repository

    async def execute(self, device_id: str, segundos: int, actor_id: UUID | None, origen: str) -> dict | None:
        """Devuelve el dispositivo actualizado, o None si el valor no cambió."""
        if not self.MINIMO_SEGUNDOS <= segundos <= self.MAXIMO_SEGUNDOS:
            raise DomainError(
                f"El intervalo debe estar entre {self.MINIMO_SEGUNDOS} y {self.MAXIMO_SEGUNDOS} segundos"
            )
        dispositivo = await self._device_repository.obtener(device_id)
        if dispositivo is None:
            raise RecursoNoEncontradoError(f"Dispositivo {device_id} no encontrado")
        anterior = dispositivo.get("intervalo_reporte_segundos")
        if anterior == segundos:
            return None
        actualizado = await self._device_repository.actualizar_intervalo_reporte(device_id, segundos)
        await self._device_repository.registrar_evento_config(
            device_id,
            campo=f"intervalo_reporte_segundos ({origen})",
            valor_anterior=str(anterior) if anterior is not None else None,
            valor_nuevo=str(segundos),
            actor_id=actor_id,
        )
        return actualizado


class ConsultarHistorialConfiguracionUseCase:
    """HU-49: historial de cambios de configuración/instalación/calibración/
    reemplazo de un dispositivo, de solo lectura."""

    def __init__(self, device_repository: IDeviceRepository) -> None:
        self._device_repository = device_repository

    async def execute(self, device_id: str) -> list[dict]:
        return await self._device_repository.listar_historial_configuracion(device_id)
