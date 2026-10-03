"""Fidelidad de `SQLAlchemyUsuarioRepository.agregar`.

Defecto que motivó estas pruebas: `agregar` construía el `UserModel` solo con
nombre, email, hash y rol. Los campos de privacidad (HU-44, Ley N.° 29733) y de
ciclo de vida (HU-45) los rellenaban los `default` del modelo, así que un
usuario creado como inactivo se guardaba activo y uno con consentimiento ya
registrado se guardaba sin él — en silencio, porque `agregar` devolvía la
entidad releída del modelo y por tanto coherente consigo misma.

Ninguna prueba lo detectaba: la suite creaba usuarios siempre con los valores
por defecto, que coinciden con los del modelo.
"""

from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.entities.usuario import PRIVACY_POLICY_VERSION, Usuario
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.security.password_hasher import hash_password

AHORA = datetime.now(timezone.utc).replace(microsecond=0)


def _normalizar(valor):
    """Compara instantes, no representaciones.

    SQLite —el motor de la suite— no almacena el desplazamiento horario, así que
    devuelve datetimes *naive* aunque la columna sea `DateTime(timezone=True)`.
    El instante es el mismo y en PostgreSQL (el motor de despliegue) el
    `tzinfo` sobrevive; comparar en crudo haría fallar la prueba por un detalle
    del dialecto en vez de por una pérdida real de datos.
    """
    if isinstance(valor, datetime) and valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor


def _usuario(email: str, **overrides) -> Usuario:
    base = {
        "nombre": "Farmacéutica Ríos",
        "email": email,
        "password_hash": hash_password("password123"),
        "rol": Rol.FARMACEUTICO,
    }
    return Usuario(**{**base, **overrides})


@pytest.mark.asyncio
async def test_persiste_el_rechazo_de_privacidad(db_session):
    repo = SQLAlchemyUsuarioRepository(db_session)

    await repo.agregar(_usuario("sin.consentimiento@upc.pe", privacy_accepted=False))
    await db_session.commit()

    recuperado = await repo.obtener_por_email("sin.consentimiento@upc.pe")
    assert recuperado is not None
    # Con el defecto, el `default=True` del modelo lo marcaba como aceptado: el
    # sistema daría por consentido a quien nunca consintió.
    assert recuperado.privacy_accepted is False
    assert recuperado.requiere_aceptar_privacidad is True


@pytest.mark.asyncio
async def test_persiste_un_consentimiento_ya_registrado(db_session):
    repo = SQLAlchemyUsuarioRepository(db_session)

    await repo.agregar(
        _usuario(
            "con.consentimiento@upc.pe",
            privacy_accepted=True,
            privacy_accepted_at=AHORA,
            privacy_version_accepted=PRIVACY_POLICY_VERSION,
        )
    )
    await db_session.commit()

    recuperado = await repo.obtener_por_email("con.consentimiento@upc.pe")
    assert recuperado is not None
    assert recuperado.privacy_version_accepted == PRIVACY_POLICY_VERSION
    assert recuperado.privacy_accepted_at is not None
    # La fecha y versión son la evidencia documental del consentimiento: sin
    # ellas no se puede acreditar cuándo ni a qué política se consintió.
    assert recuperado.requiere_aceptar_privacidad is False


@pytest.mark.asyncio
async def test_persiste_un_usuario_creado_como_inactivo(db_session):
    repo = SQLAlchemyUsuarioRepository(db_session)

    await repo.agregar(
        _usuario(
            "inactivo@upc.pe",
            is_active=False,
            motivo_desactivacion="alta_revocada",
            desactivado_en=AHORA - timedelta(minutes=5),
        )
    )
    await db_session.commit()

    recuperado = await repo.obtener_por_email("inactivo@upc.pe")
    assert recuperado is not None
    # Con el defecto quedaba activo y podía iniciar sesión (HU-45).
    assert recuperado.is_active is False
    assert recuperado.motivo_desactivacion == "alta_revocada"
    assert recuperado.desactivado_en is not None


@pytest.mark.asyncio
async def test_persiste_la_marca_de_anonimizacion(db_session):
    repo = SQLAlchemyUsuarioRepository(db_session)

    await repo.agregar(_usuario("anonimo@upc.pe", anonymized_for_gdpr=True))
    await db_session.commit()

    recuperado = await repo.obtener_por_email("anonimo@upc.pe")
    assert recuperado is not None
    assert recuperado.anonymized_for_gdpr is True


@pytest.mark.asyncio
async def test_persiste_quien_desactivo_al_usuario(db_session):
    repo = SQLAlchemyUsuarioRepository(db_session)
    admin = await repo.agregar(_usuario("admin.baja@upc.pe", rol=Rol.ADMINISTRADOR))
    await db_session.commit()

    await repo.agregar(
        _usuario("dado.de.baja@upc.pe", is_active=False, desactivado_por=admin.id)
    )
    await db_session.commit()

    recuperado = await repo.obtener_por_email("dado.de.baja@upc.pe")
    assert recuperado is not None
    # La trazabilidad de quién ejecutó la baja es parte del expediente de HU-45.
    assert recuperado.desactivado_por == admin.id


@pytest.mark.asyncio
async def test_agregar_no_pierde_ningun_campo_de_la_entidad(db_session):
    """Guarda contra la reaparición del defecto al añadir campos nuevos.

    `_to_entity` lee trece campos y `agregar` escribía cuatro: esa asimetría era
    el defecto. Esta prueba la comprueba de forma genérica recorriendo los
    campos del dataclass, así que un campo añadido a `Usuario` y olvidado en
    `agregar` la rompe sin tener que escribir una prueba nueva.
    """
    repo = SQLAlchemyUsuarioRepository(db_session)
    admin = await repo.agregar(_usuario("admin.completo@upc.pe", rol=Rol.ADMINISTRADOR))
    await db_session.commit()

    # Todos los campos con valores distintos de su default, para que ningún
    # acierto sea casual.
    original = _usuario(
        "completo@upc.pe",
        rol=Rol.TECNICO,
        privacy_accepted=False,
        privacy_accepted_at=AHORA - timedelta(days=1),
        privacy_version_accepted="0.9",
        is_active=False,
        motivo_desactivacion="cese",
        desactivado_en=AHORA - timedelta(hours=2),
        desactivado_por=admin.id,
        anonymized_for_gdpr=True,
    )

    devuelto = await repo.agregar(original)
    await db_session.commit()
    releido = await repo.obtener_por_id(devuelto.id)
    assert releido is not None

    # `id` lo asigna la base de datos; el resto debe viajar intacto.
    comparables = [f.name for f in fields(Usuario) if f.name != "id"]
    diferencias = {
        campo: (getattr(original, campo), getattr(releido, campo))
        for campo in comparables
        if _normalizar(getattr(original, campo)) != _normalizar(getattr(releido, campo))
    }
    assert diferencias == {}, f"agregar() no persistió: {sorted(diferencias)}"


@pytest.mark.asyncio
async def test_el_alta_normal_sigue_exigiendo_consentimiento(db_session):
    """El caso de uso de alta construye la entidad con los valores por defecto.

    Se fija aquí para que el arreglo no haya cambiado de paso el flujo real:
    un usuario nuevo debe seguir teniendo que aceptar la política, porque su
    `privacy_version_accepted` es `None` y no coincide con la vigente.
    """
    repo = SQLAlchemyUsuarioRepository(db_session)

    await repo.agregar(_usuario("nuevo@upc.pe"))
    await db_session.commit()

    recuperado = await repo.obtener_por_email("nuevo@upc.pe")
    assert recuperado is not None
    assert recuperado.requiere_aceptar_privacidad is True
