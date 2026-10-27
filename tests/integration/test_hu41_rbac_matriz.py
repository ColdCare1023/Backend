"""HU-41: gestión de usuarios y RBAC.

`test_la_matriz_de_permisos_es_la_declarada` GENERA la matriz desde las rutas
registradas (dependencias `require_roles`) y la compara con la tabla 8.3 de la
especificación: si alguien cambia un permiso sin actualizar la tabla, falla.
El administrador tiene acceso implícito a todo (rbac.verificar_permiso), así
que la tabla lista solo los roles EXPLÍCITOS."""

import importlib
import pkgutil
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

import src.interface.api as api
from src.application.use_cases.gestionar_usuarios import CambiarRolUsuarioUseCase
from src.domain.entities.usuario import Usuario
from src.domain.exceptions import DomainError
from src.domain.value_objects.rol import Rol
from src.infrastructure.database.models import AuditLogModel
from tests.conftest import auth_header

A, F, T, AU = "administrador", "farmaceutico", "tecnico", "auditor"
PUBLICO, AUTENTICADO = "PUBLICO", "AUTENTICADO"

MATRIZ_ESPERADA: dict[tuple[str, str], frozenset[str] | str] = {
    ("GET", "/api/alertas"): frozenset({AU, F, T}),
    ("PATCH", "/api/alertas/{alerta_id}/revisar"): frozenset({F}),
    ("GET", "/api/alertas/{alerta_id}/acciones-correctivas"): frozenset({AU, F, T}),
    ("POST", "/api/alertas/{alerta_id}/acciones-correctivas"): frozenset({F, T}),
    ("POST", "/api/alertas/acciones-correctivas/{accion_id}/rectificar"): frozenset({F, T}),
    ("GET", "/api/auditoria"): frozenset({A, AU}),
    ("POST", "/api/auth/login"): PUBLICO,
    ("POST", "/api/auth/google"): PUBLICO,
    ("POST", "/api/auth/sse-ticket"): AUTENTICADO,
    ("POST", "/api/auth/privacidad/aceptar"): AUTENTICADO,
    ("POST", "/api/auth/privacidad/rechazar"): AUTENTICADO,
    ("POST", "/api/auth/logout"): AUTENTICADO,
    # BE-025: la contraseña temporal se cambia con la sesión del propio usuario.
    ("POST", "/api/auth/cambiar-password"): AUTENTICADO,
    # Recuperación de cuenta por correo (token de un solo uso + rate limit).
    ("POST", "/api/auth/recuperar-password"): PUBLICO,
    ("POST", "/api/auth/restablecer-password"): PUBLICO,
    ("POST", "/api/checklist-bpa"): frozenset({A, F}),
    ("GET", "/api/checklist-bpa"): frozenset({A, F}),
    ("GET", "/api/checklist-bpa/historial"): frozenset({A, F}),
    ("GET", "/api/dispositivos"): frozenset({A}),
    ("POST", "/api/dispositivos"): frozenset({A}),
    ("GET", "/api/dispositivos/estado-operativo"): frozenset({AU, F, T}),
    ("GET", "/api/dispositivos/incidentes"): frozenset({A, AU, F, T}),
    ("GET", "/api/dispositivos/calibracion/estado"): frozenset({A, F}),
    ("PATCH", "/api/dispositivos/{device_id}/calibracion"): frozenset({A, F}),
    ("POST", "/api/dispositivos/{device_id}/baja"): frozenset({A}),
    ("POST", "/api/dispositivos/{device_id}/credenciales/rotar"): frozenset({A}),
    ("POST", "/api/dispositivos/{device_id}/credenciales/revocar"): frozenset({A}),
    ("PATCH", "/api/dispositivos/{device_id}/instalacion"): frozenset({A}),
    ("PATCH", "/api/dispositivos/{device_id}/responsable"): frozenset({A}),
    ("POST", "/api/dispositivos/{device_id}/broker/confirmar"): frozenset({A}),
    ("PATCH", "/api/dispositivos/{device_id}/intervalo"): frozenset({A}),
    ("GET", "/api/dispositivos/{device_id}/historial-configuracion"): frozenset({A, AU}),
    ("POST", "/api/firmware/releases"): frozenset({A}),
    ("GET", "/api/firmware/releases"): frozenset({A}),
    ("POST", "/api/firmware/despliegues"): frozenset({A}),
    ("POST", "/api/firmware/despliegues/{despliegue_id}/ejecutar"): frozenset({A}),
    ("GET", "/api/ia/modelo"): frozenset({AU, F}),
    ("POST", "/api/ia/clasificar"): frozenset({F}),
    ("POST", "/api/ia/modelo/versiones"): frozenset({A}),
    ("GET", "/api/ia/modelo/versiones"): frozenset({A, AU}),
    ("POST", "/api/ia/modelo/versiones/{version}/activar"): frozenset({A}),
    ("GET", "/api/ia/inferencia/{lectura_id}"): frozenset({AU, F}),
    ("GET", "/api/ia/preventivo"): frozenset({A, AU, F}),
    ("GET", "/api/ia/preventivo/predicciones"): frozenset({AU, F}),
    ("POST", "/api/lecturas"): frozenset({F, T}),
    ("GET", "/api/lecturas"): frozenset({AU, F, T}),
    ("GET", "/api/lecturas/{lectura_id}"): frozenset({AU, F, T}),
    ("GET", "/api/reportes/bpa"): frozenset({AU, F}),
    ("GET", "/api/reportes/bpa/pdf"): frozenset({A, AU, F}),
    # Autenticado por ticket SSE de un solo uso (EventSource no envía headers).
    ("GET", "/api/sse/lecturas"): PUBLICO,
    ("GET", "/api/trazabilidad"): frozenset({AU, F, T}),
    ("GET", "/api/trazabilidad/verificar"): frozenset({AU, F, T}),
    ("GET", "/api/trazabilidad/verificar-dispositivo"): frozenset({AU, F, T}),
    ("GET", "/api/trazabilidad/estado"): frozenset({AU, F, T}),
    ("POST", "/api/trazabilidad/corrupcion/{registro_id}/aislar"): frozenset({A}),
    ("POST", "/api/usuarios"): frozenset({A}),
    ("GET", "/api/usuarios"): frozenset({A}),
    ("PATCH", "/api/usuarios/{usuario_id}/desactivar"): frozenset({A}),
    ("PATCH", "/api/usuarios/{usuario_id}/rol"): frozenset({A}),
    ("POST", "/api/usuarios/{usuario_id}/reactivar"): frozenset({A}),
    ("POST", "/api/usuarios/{usuario_id}/restablecer-password"): frozenset({A}),
}


def _proteccion(dependant) -> tuple[frozenset[str] | None, bool]:
    roles, autenticado = None, False
    for dependencia in dependant.dependencies:
        nombre = getattr(dependencia.call, "__qualname__", "")
        if nombre == "require_roles.<locals>.dependencia":
            roles = frozenset(r.value for r in dependencia.call.__closure__[0].cell_contents)
        if nombre.startswith("get_current_user"):
            autenticado = True
        sub_roles, sub_aut = _proteccion(dependencia)
        roles, autenticado = roles or sub_roles, autenticado or sub_aut
    return roles, autenticado


def _matriz_real() -> dict[tuple[str, str], frozenset[str] | str]:
    matriz = {}
    for modulo in pkgutil.iter_modules(api.__path__):
        if not modulo.name.endswith("_router"):
            continue
        router = importlib.import_module(f"src.interface.api.{modulo.name}").router
        for ruta in router.routes:
            if not isinstance(ruta, APIRoute):
                continue
            roles, autenticado = _proteccion(ruta.dependant)
            for metodo in ruta.methods:
                matriz[(metodo, ruta.path)] = roles or (AUTENTICADO if autenticado else PUBLICO)
    return matriz


def test_la_matriz_de_permisos_es_la_declarada():
    real = _matriz_real()
    assert set(real) == set(MATRIZ_ESPERADA), "Endpoints nuevos o eliminados: actualizar la tabla 8.3"
    diferencias = {k: (MATRIZ_ESPERADA[k], v) for k, v in real.items() if MATRIZ_ESPERADA[k] != v}
    assert diferencias == {}


# ── Denegación coherente (criterio 5) ───────────────────────────────────

ENDPOINT_FUERA_DE_ROL = {
    "farmaceutico": ("GET", "/api/usuarios"),
    "tecnico": ("PATCH", "/api/alertas/00000000-0000-0000-0000-000000000000/revisar"),
    "auditor": ("POST", "/api/lecturas"),
}


@pytest.mark.parametrize("rol", sorted(ENDPOINT_FUERA_DE_ROL))
def test_cada_rol_recibe_403_fuera_de_su_columna(rol, request, client):
    token = request.getfixturevalue(f"token_{rol}")
    metodo, ruta = ENDPOINT_FUERA_DE_ROL[rol]
    respuesta = client.request(metodo, ruta, headers=auth_header(token), json={})
    assert respuesta.status_code == 403


def test_sin_token_es_401(client):
    assert client.get("/api/usuarios").status_code == 401


def test_token_de_usuario_desactivado_es_401(client, token_admin, token_tecnico):
    tecnico = next(u for u in client.get("/api/usuarios", headers=auth_header(token_admin)).json()
                   if u["rol"] == "tecnico")
    client.patch(f"/api/usuarios/{tecnico['id']}/desactivar", json={"motivo": "renuncia"},
                 headers=auth_header(token_admin))
    assert client.get("/api/alertas", headers=auth_header(token_tecnico)).status_code == 401


def test_el_cambio_de_rol_rige_en_la_siguiente_peticion(client, token_admin, token_tecnico):
    """BE-009: el token emitido con el rol anterior deja de valer; el nuevo
    rol rige desde el siguiente inicio de sesión."""
    assert client.get("/api/auditoria", headers=auth_header(token_tecnico)).status_code == 403
    tecnico = next(u for u in client.get("/api/usuarios", headers=auth_header(token_admin)).json()
                   if u["rol"] == "tecnico")
    client.patch(f"/api/usuarios/{tecnico['id']}/rol", json={"rol": "auditor"}, headers=auth_header(token_admin))
    assert client.get("/api/auditoria", headers=auth_header(token_tecnico)).status_code == 401


# ── Protección del último administrador (criterio 3) ────────────────────


def _yo(client, token) -> dict:
    return next(u for u in client.get("/api/usuarios", headers=auth_header(token)).json()
                if u["rol"] == "administrador")


async def _acciones(factory, accion: str) -> list[dict]:
    async with factory() as session:
        filas = (await session.execute(select(AuditLogModel).where(AuditLogModel.accion == accion))).scalars()
        return [f.detalle for f in filas]


@pytest.mark.asyncio
async def test_el_admin_no_cambia_su_propio_rol(client, token_admin, db_session_factory):
    yo = _yo(client, token_admin)
    respuesta = client.patch(f"/api/usuarios/{yo['id']}/rol", json={"rol": "tecnico"}, headers=auth_header(token_admin))
    assert respuesta.status_code == 409
    assert await _acciones(db_session_factory, "CAMBIAR_ROL_USUARIO") == []


@pytest.mark.asyncio
async def test_el_admin_no_se_desactiva_a_si_mismo(client, token_admin, db_session_factory):
    yo = _yo(client, token_admin)
    respuesta = client.patch(
        f"/api/usuarios/{yo['id']}/desactivar", json={"motivo": "renuncia"}, headers=auth_header(token_admin)
    )
    assert respuesta.status_code == 409
    assert await _acciones(db_session_factory, "DESACTIVAR_USUARIO") == []


def test_un_admin_si_puede_quitar_el_rol_a_otro_admin(client, token_admin):
    otro = client.post(
        "/api/usuarios",
        json={"nombre": "Admin Dos", "email": "admin2@farmacia.example.org",
              "password": "Contrasena-Segura-2026", "rol": "administrador"},
        headers=auth_header(token_admin),
    ).json()
    respuesta = client.patch(f"/api/usuarios/{otro['id']}/rol", json={"rol": "auditor"}, headers=auth_header(token_admin))
    assert respuesta.status_code == 200


class _RepoUnSoloAdmin:
    def __init__(self, objetivo: Usuario) -> None:
        self._objetivo = objetivo

    async def obtener_por_id(self, usuario_id):
        return self._objetivo

    async def contar_administradores_activos(self) -> int:
        return 1

    async def actualizar(self, usuario):
        return usuario


@pytest.mark.asyncio
async def test_nunca_queda_cero_administradores():
    """Defensa en profundidad: por la API un admin activo siempre cuenta, pero
    el caso de uso no debe depender de eso (concurrencia, scripts)."""
    unico = Usuario(id=uuid4(), nombre="Único", email="unico@x.pe", password_hash="h", rol=Rol.ADMINISTRADOR)
    with pytest.raises(DomainError, match="al menos un administrador"):
        await CambiarRolUsuarioUseCase(_RepoUnSoloAdmin(unico)).execute(unico.id, Rol.TECNICO, actor_id=uuid4())


# ── Privacidad (criterios 1 y 4) ────────────────────────────────────────


def test_usuario_desactivado_se_lista_anonimizado(client, token_admin, token_tecnico):
    tecnico = next(u for u in client.get("/api/usuarios", headers=auth_header(token_admin)).json()
                   if u["rol"] == "tecnico")
    client.patch(f"/api/usuarios/{tecnico['id']}/desactivar", json={"motivo": "renuncia"},
                 headers=auth_header(token_admin))
    listado = {u["id"]: u for u in client.get("/api/usuarios", headers=auth_header(token_admin)).json()}
    assert listado[tecnico["id"]]["nombre"] == "Usuario desactivado"
    assert listado[tecnico["id"]]["email"] == "—"
