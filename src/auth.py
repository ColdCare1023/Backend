"""Autenticación segura con Argon2id, JWT en memoria y protección contra fuerza bruta (HU-39).

Criterios de aceptación implementados:
  - Escenario 1: Login exitoso con credenciales válidas verificadas con Argon2id emite JWT con identidad y rol.
  - Escenario 2: Correo inexistente o contraseña errónea se rechazan con mensaje genérico ("Credenciales inválidas") y quedan auditados sin revelar qué dato falló.
  - Escenario 3: Bloqueo temporal tras superar umbral de 5 intentos fallidos (rate limiting / account lockout).
  - Escenario 4: Usuario desactivado no puede autenticarse.
  - [FE/BE]: Detección de token JWT expirado para forzar reautenticación en el cliente.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr

logger = logging.getLogger("coldcare.auth")
hasher = PasswordHasher()

JWT_SECRET = "coldcare_super_secret_jwt_key_2026_tesis"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 15
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 900  # 15 minutos de bloqueo


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict[str, Any]


class UsuarioEnBD:
    def __init__(self, email: str, password_raw: str, role: str = "tecnico", is_active: bool = True):
        self.email = email
        self.password_hash = hasher.hash(password_raw)
        self.role = role
        self.is_active = is_active
        self.failed_attempts = 0
        self.locked_until: float | None = None


class AlmacenUsuarios:
    def __init__(self):
        self.usuarios: dict[str, UsuarioEnBD] = {}
        self.audit_log: list[dict[str, Any]] = []
        self.revoked_tokens: set[str] = set()

        # Sembrar usuario por defecto
        self.registrar("diego.soto@coldcare.pe", "Pass_Segura_2026!", role="administrador")
        self.registrar("tecnico@coldcare.pe", "Tecnico_12345!", role="tecnico")
        self.registrar("inactivo@coldcare.pe", "Inactivo_12345!", role="tecnico", is_active=False)

    def registrar(self, email: str, password_raw: str, role: str = "tecnico", is_active: bool = True):
        self.usuarios[email.lower()] = UsuarioEnBD(email.lower(), password_raw, role, is_active)

    def auditar(self, email: str, ip: str, resultado: str, motivo: str | None = None):
        self.audit_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "email": email,
            "ip": ip,
            "resultado": resultado,
            "motivo": motivo,
        })


almacen_usuarios_global = AlmacenUsuarios()
auth_scheme = HTTPBearer(auto_error=False)


def crear_access_token(email: str, role: str, exp_seconds: int = JWT_EXPIRATION_MINUTES * 60) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_seconds)).timestamp()),
        "jti": f"{email}_{time.time_ns()}",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verificar_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        jti = payload.get("jti")
        if jti and jti in almacen_usuarios_global.revoked_tokens:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revocado")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(auth_scheme)) -> dict[str, Any]:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Falta encabezado de autenticación")
    return verificar_access_token(credentials.credentials)


auth_router = APIRouter(prefix="/api/v1/auth", tags=["Autenticación"])


@auth_router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request):
    email = req.email.lower()
    ip = request.client.host if request.client else "127.0.0.1"
    now_ts = time.time()

    user = almacen_usuarios_global.usuarios.get(email)

    # 1. Comprobar bloqueo por fuerza bruta
    if user and user.locked_until:
        if now_ts < user.locked_until:
            restante = int(user.locked_until - now_ts)
            almacen_usuarios_global.auditar(email, ip, "bloqueado", f"Cuenta bloqueada por {restante} s")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Cuenta temporalmente bloqueada por reiterados intentos fallidos. Reintente en {restante} segundos.",
            )
        else:
            # Desbloqueo tras ventana expirada
            user.locked_until = None
            user.failed_attempts = 0

    # 2. Validar existencia y estado activo
    if not user or not user.is_active:
        almacen_usuarios_global.auditar(email, ip, "rechazado", "Usuario no existe o desactivado")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
        )

    # 3. Verificar contraseña con Argon2id
    try:
        hasher.verify(user.password_hash, req.password)
        # Login correcto: reiniciar contadores
        user.failed_attempts = 0
        user.locked_until = None
        almacen_usuarios_global.auditar(email, ip, "exitoso", None)

        token = crear_access_token(user.email, user.role)
        return LoginResponse(
            access_token=token,
            expires_in=JWT_EXPIRATION_MINUTES * 60,
            user={"email": user.email, "role": user.role},
        )
    except VerifyMismatchError:
        user.failed_attempts += 1
        almacen_usuarios_global.auditar(email, ip, "rechazado", f"Contraseña errónea (intento {user.failed_attempts})")

        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now_ts + LOCKOUT_SECONDS
            almacen_usuarios_global.auditar(email, ip, "bloqueo_activado", f"Bloqueo activado por {LOCKOUT_SECONDS} s")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Cuenta temporalmente bloqueada por demasiados intentos fallidos.",
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
        )


@auth_router.post("/logout")
def logout(current_user: dict[str, Any] = Depends(get_current_user)):
    jti = current_user.get("jti")
    if jti:
        almacen_usuarios_global.revoked_tokens.add(jti)
    return {"status": "ok", "message": "Sesión cerrada correctamente"}


@auth_router.get("/perfil")
def perfil(current_user: dict[str, Any] = Depends(get_current_user)):
    return {"email": current_user["sub"], "role": current_user["role"], "active": True}
