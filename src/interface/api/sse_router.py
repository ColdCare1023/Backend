import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from src.domain.exceptions import CredencialesInvalidasError
from src.infrastructure.database.repositories.jti_repository import MOTIVO_TICKET_SSE, SQLAlchemyJtiRepository
from src.infrastructure.database.repositories.usuario_repository import SQLAlchemyUsuarioRepository
from src.infrastructure.security.jwt_handler import TicketSSE
from src.interface.api.deps import DbSessionDep, JWTHandlerDep

router = APIRouter(prefix="/api/sse", tags=["sse"])

# Devuelve False en cuanto la sesión que abrió el stream deja de ser válida.
SesionVigente = Callable[[], Awaitable[bool]]


async def sesion_del_ticket_vigente(fabrica, ticket: TicketSSE) -> bool:
    """BE-010: la sesión que abrió el stream sigue valiendo.

    No basta con que no se haya cerrado con logout: el access token padre no
    debe haber caducado, y el usuario debe seguir activo y sin sesiones
    invalidadas (desactivación HU-45, cambio de rol o de contraseña)."""
    if ticket.token_padre_exp is not None and datetime.now(tz=timezone.utc) >= ticket.token_padre_exp:
        return False
    async with fabrica() as sesion:
        if ticket.token_padre_jti is not None and await SQLAlchemyJtiRepository(sesion).esta_invalidado(
            ticket.token_padre_jti
        ):
            return False
        usuario = await SQLAlchemyUsuarioRepository(sesion).obtener_por_id(ticket.usuario_id)
    if usuario is None or not usuario.is_active:
        return False
    return ticket.token_padre_iat is None or usuario.token_vigente(ticket.token_padre_iat)


async def _event_stream(request: Request, sesion_vigente: SesionVigente | None = None) -> AsyncGenerator[str, None]:
    broadcaster = request.app.state.sse_broadcaster
    queue = broadcaster.subscribe()
    try:
        # Chunk inicial: fuerza el envío de headers a través de proxies y
        # dispara el evento `open` del EventSource del navegador.
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            # La sesión se comprueba en CADA vuelta, no solo al abrir el stream.
            # Un stream SSE vive horas: si solo se validara al principio, cerrar
            # sesión dejaría el caudal de datos térmicos fluyendo hasta que el
            # navegador se cerrase, contradiciendo la revocación de JWT que el
            # sistema declara como control de seguridad (RF-17).
            if sesion_vigente is not None and not await sesion_vigente():
                # El navegador reconecta con un ticket nuevo; si la sesión ya
                # no vale, ese ticket se rechaza y el cliente vuelve al login.
                yield "event: sesion_expirada\ndata: {}\n\n"
                break
            try:
                mensaje = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield f"{mensaje}\n\n"
            except TimeoutError:
                yield ": keep-alive\n\n"
    finally:
        broadcaster.unsubscribe(queue)


@router.get("/lecturas")
async def stream_lecturas(
    request: Request, ticket: str, jwt_handler: JWTHandlerDep, session: DbSessionDep
) -> StreamingResponse:
    """RF-11: el dashboard consume actualizaciones en tiempo real vía SSE.

    Requiere un ticket efímero emitido en POST /api/auth/sse-ticket porque
    EventSource no puede enviar el header Authorization. El ticket es de UN
    SOLO USO: viaja como query param (puede quedar en logs de proxies), así
    que reutilizarlo se rechaza aunque no haya expirado.

    BE-009: el consumo del ticket y la revocación de la sesión se resuelven
    contra `jti_invalidados`, comunes a todas las instancias del backend.
    """
    try:
        datos_ticket = jwt_handler.validar_ticket_sse(ticket)
    except CredencialesInvalidasError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    jtis = SQLAlchemyJtiRepository(session)
    if not await jtis.invalidar(datos_ticket.jti, datos_ticket.exp, MOTIVO_TICKET_SSE):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Ticket SSE ya utilizado"
        )
    await session.commit()

    fabrica = request.app.state.session_factory
    # Un ticket cuya sesión ya se cerró o caducó entre la emisión y el uso no
    # abre nada.
    if not await sesion_del_ticket_vigente(fabrica, datos_ticket):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="La sesión fue cerrada"
        )

    async def sesion_vigente() -> bool:
        return await sesion_del_ticket_vigente(fabrica, datos_ticket)

    return StreamingResponse(
        _event_stream(request, sesion_vigente),
        media_type="text/event-stream",
    )
