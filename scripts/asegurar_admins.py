"""Asegura que ciertas personas sean administradoras activas (idempotente).

Uso (dentro del contenedor, p. ej. `railway ssh`):

    python -m scripts.asegurar_admins --email ana@farmacia.pe --nombre "Ana Pérez" \\
                                      --email luis@farmacia.pe --nombre "Luis Soto"

Para cada par correo/nombre:

- si la cuenta no existe, la crea con rol ADMINISTRADOR, activa, con una
  contraseña aleatoria inutilizable (nadie la conoce: nunca se imprime ni se
  guarda) y sin contraseña temporal pendiente;
- si existe, la deja con rol ADMINISTRADOR y activa. Si cambió el rol, cierra
  sus sesiones abiertas (el token viaja con el rol anterior). Si estaba
  desactivada, la reactiva como ReactivarUsuarioUseCase: sustituye la
  contraseña por una inutilizable (la anterior pudo ser la razón de la baja),
  cierra sus sesiones y anula enlaces y códigos pendientes; la persona elige
  una contraseña nueva con el código de activación;
- con el correo configurado (EMAIL_ENABLED y su transporte), emite un código de
  activación (CODIGO_ACTIVACION_TTL_HORAS) y envía el correo de activación: la
  persona elige su contraseña en FRONTEND_URL/recuperar-contrasena.

Volver a ejecutarlo no duplica nada; solo reenvía un código nuevo (el anterior
deja de valer). `--sin-correo` omite el código y el envío; una cuenta creada o
reactivada sin correo queda sin contraseña utilizable y la salida lo indica
(un administrador puede asignarle una temporal desde la gestión de usuarios).

A diferencia de `scripts.crear_admin`, no se limita al primer administrador.
Solo imprime correos enmascarados y el estado de cada cuenta.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.use_cases.auditar_accion_critica import AuditarAccionCriticaUseCase
from src.application.use_cases.codigos_verificacion import PoliticaCodigos, ServicioCodigosVerificacion
from src.domain.entities.usuario import Usuario
from src.domain.value_objects.rol import Rol
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.database.repositories.audit_log_repository import SQLAlchemyAuditLogRepository
from src.infrastructure.database.repositories.codigo_verificacion_repository import (
    SQLAlchemyCodigoVerificacionRepository,
)
from src.infrastructure.database.repositories.password_reset_repository import (
    SQLAlchemyPasswordResetRepository,
)
from src.infrastructure.database.repositories.trazabilidad_repository import SQLAlchemyTrazabilidadRepository
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.notifications.bienvenida import ENVIADO, NO_CONFIGURADO, NotificadorBienvenida
from src.infrastructure.security.password_hasher import hash_password_async
from src.infrastructure.security.privacidad import enmascarar_email

CREADO = "creado"
ACTUALIZADO = "actualizado"
SIN_CAMBIOS = "sin_cambios"
OMITIDO = "omitido"


@dataclass(frozen=True, slots=True)
class Admin:
    email: str
    nombre: str


REACTIVADO = "reactivado"


@dataclass(frozen=True, slots=True)
class Resultado:
    email_enmascarado: str
    estado: str
    correo: str
    # La cuenta solo tiene la contraseña inutilizable y no se envió código.
    sin_password: bool = False

    def linea(self) -> str:
        linea = f"{self.email_enmascarado}: cuenta {self.estado}; correo de activación {self.correo}"
        if self.sin_password:
            linea += (
                "; AVISO: la cuenta no tiene contraseña utilizable (sin código de activación). "
                "Asígnele una temporal desde la gestión de usuarios o vuelva a ejecutar con correo."
            )
        return linea


def _validar(emails: list[str], nombres: list[str]) -> list[Admin]:
    if not emails:
        raise SystemExit("Indique al menos un --email con su --nombre.")
    if len(emails) != len(nombres):
        raise SystemExit("Cada --email necesita su --nombre (en el mismo orden).")
    admins: list[Admin] = []
    vistos: set[str] = set()
    for crudo, nombre in zip(emails, nombres, strict=True):
        try:
            email = str(TypeAdapter(EmailStr).validate_python(crudo.strip())).lower()
        except ValidationError:
            raise SystemExit(f"Correo inválido: {enmascarar_email(crudo.strip())}") from None
        nombre = nombre.strip()
        if not 1 <= len(nombre) <= 120:
            raise SystemExit(f"El nombre de {enmascarar_email(email)} debe tener entre 1 y 120 caracteres.")
        if email in vistos:
            raise SystemExit(f"Correo repetido: {enmascarar_email(email)}")
        vistos.add(email)
        admins.append(Admin(email=email, nombre=nombre))
    return admins


async def _password_inutilizable() -> str:
    """Hash de un secreto aleatorio de 384 bits que se descarta al instante:
    la cuenta no admite login con contraseña hasta que el titular elija una."""
    return await hash_password_async(secrets.token_urlsafe(48) + "A1")


async def _asegurar_cuenta(session: AsyncSession, admin: Admin) -> tuple[Usuario, str, bool]:
    """Devuelve la cuenta, su estado y si su contraseña es la inutilizable recién puesta."""
    repo = SQLAlchemyUsuarioRepository(session)
    existente = await repo.obtener_por_email(admin.email)
    if existente is None:
        nuevo = Usuario(
            nombre=admin.nombre,
            email=admin.email,
            password_hash=await _password_inutilizable(),
            rol=Rol.ADMINISTRADOR,
            is_active=True,
            debe_cambiar_password=False,
        )
        return await repo.agregar(nuevo), CREADO, True

    if existente.rol == Rol.ADMINISTRADOR and existente.is_active:
        return existente, SIN_CAMBIOS, False
    ahora = datetime.now(tz=timezone.utc)
    reactivada = not existente.is_active
    if existente.rol != Rol.ADMINISTRADOR:
        # Una cuenta activa que se promueve conserva su contraseña (la usa su
        # titular); el token anterior lleva el rol viejo y se invalida.
        existente.rol = Rol.ADMINISTRADOR
        existente.invalidar_sesiones(ahora)
    if reactivada:
        existente.is_active = True
        existente.motivo_desactivacion = None
        existente.desactivado_en = None
        existente.desactivado_por = None
        existente.anonymized_for_gdpr = False
        # Como ReactivarUsuarioUseCase: la contraseña anterior pudo ser la
        # razón de la baja. Se sustituye por una inutilizable (también cierra
        # las sesiones) y se anulan enlaces y códigos pendientes; la persona
        # elige una nueva con el código de activación.
        existente.establecer_password(await _password_inutilizable(), ahora, temporal=False)
        await SQLAlchemyPasswordResetRepository(session).anular_pendientes(existente.id)
        await SQLAlchemyCodigoVerificacionRepository(session).reiniciar(existente.id)
    return await repo.actualizar(existente), REACTIVADO if reactivada else ACTUALIZADO, reactivada


async def asegurar(
    admins: list[Admin],
    fabrica_sesiones: async_sessionmaker[AsyncSession],
    settings: Settings,
    notificador: NotificadorBienvenida | None = None,
    enviar_correo: bool = True,
) -> list[Resultado]:
    notificador = notificador if notificador is not None else NotificadorBienvenida(settings)
    resultados: list[Resultado] = []
    for admin in admins:
        async with fabrica_sesiones() as session:
            usuario, estado, sin_password = await _asegurar_cuenta(session, admin)
            activacion = None
            if enviar_correo and notificador.correo_configurado:
                codigos = ServicioCodigosVerificacion(
                    SQLAlchemyCodigoVerificacionRepository(session),
                    settings.clave_codigos,
                    PoliticaCodigos.desde_settings(settings),
                )
                activacion = await notificador.preparar_activacion(usuario, codigos)
            await AuditarAccionCriticaUseCase(
                SQLAlchemyAuditLogRepository(session), SQLAlchemyTrazabilidadRepository(session)
            ).execute(
                usuario_id=None,
                accion="ASEGURAR_ADMINISTRADOR",
                recurso=f"usuarios/{usuario.id}",
                detalle={
                    "email": enmascarar_email(admin.email),
                    "estado": estado,
                    "codigo_activacion": activacion is not None,
                    "origen": "scripts.asegurar_admins",
                },
                ip_origen=None,
            )
            await session.commit()
        if activacion is not None:
            correo = await notificador.enviar_activacion(activacion)
        else:
            correo = OMITIDO if not enviar_correo else NO_CONFIGURADO
        # Con el correo enviado, la persona elige su contraseña con el código.
        resultados.append(
            Resultado(enmascarar_email(admin.email), estado, correo, sin_password=sin_password and correo != ENVIADO)
        )
    return resultados


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.asegurar_admins",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--email", action="append", default=[], help="Correo del administrador (repetible)")
    parser.add_argument("--nombre", action="append", default=[], help="Nombre completo (uno por --email)")
    parser.add_argument("--sin-correo", action="store_true", help="No emitir código ni enviar el correo")
    return parser


async def _principal(argv: list[str] | None) -> int:
    args = _parser().parse_args(argv)
    admins = _validar(args.email, args.nombre)
    from src.infrastructure.database.session import _session_factory

    resultados = await asegurar(admins, _session_factory, get_settings(), enviar_correo=not args.sin_correo)
    for resultado in resultados:
        print(resultado.linea())
    fallidos = [r for r in resultados if r.correo not in (ENVIADO, OMITIDO, NO_CONFIGURADO)]
    return 1 if fallidos else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_principal(argv))


if __name__ == "__main__":
    sys.exit(main())
