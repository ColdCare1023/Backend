from datetime import datetime, timezone

from src.application.use_cases.registrar_hash_encadenado import RegistrarHashEncadenadoUseCase
from src.domain.entities.usuario import PRIVACY_POLICY_VERSION, Usuario
from src.domain.repositories.i_usuario_repository import IUsuarioRepository


class AceptarPrivacidadUseCase:
    """HU-44: registra el consentimiento explícito de la Ley N.° 29733.

    El evento queda encadenado criptográficamente igual que el resto de la
    trazabilidad (reutiliza RegistrarHashEncadenadoUseCase, HU-24/25) — no es
    un registro aparte fuera de la cadena de auditoría.
    """

    def __init__(
        self,
        usuario_repository: IUsuarioRepository,
        registrar_hash: RegistrarHashEncadenadoUseCase,
    ) -> None:
        self._usuario_repository = usuario_repository
        self._registrar_hash = registrar_hash

    async def execute(self, usuario: Usuario, ip_origen: str | None) -> Usuario:
        usuario.privacy_accepted = True
        usuario.privacy_accepted_at = datetime.now(tz=timezone.utc)
        usuario.privacy_version_accepted = PRIVACY_POLICY_VERSION
        actualizado = await self._usuario_repository.actualizar(usuario)

        await self._registrar_hash.execute(
            tipo_evento="ACEPTACION_PRIVACIDAD",
            payload={
                "usuario_id": str(usuario.id),
                "email": usuario.email,
                "ip_origen": ip_origen,
                "version_politica_aceptada": PRIVACY_POLICY_VERSION,
            },
            usuario_id=usuario.id,
        )
        return actualizado


class RechazarPrivacidadUseCase:
    """HU-44 Escenario 3: el rechazo revoca el token recién emitido (por jti)
    y deja constancia auditable; en el siguiente login se vuelve a pedir."""

    def __init__(self, registrar_hash: RegistrarHashEncadenadoUseCase) -> None:
        self._registrar_hash = registrar_hash

    async def execute(self, usuario: Usuario, ip_origen: str | None) -> None:
        await self._registrar_hash.execute(
            tipo_evento="RECHAZO_PRIVACIDAD",
            payload={"usuario_id": str(usuario.id), "email": usuario.email, "ip_origen": ip_origen},
            usuario_id=usuario.id,
        )
