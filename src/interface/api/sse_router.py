import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from src.domain.exceptions import CredencialesInvalidasError
from src.interface.api.deps import JWTHandlerDep

router = APIRouter(prefix="/api/sse", tags=["sse"])


async def _event_stream(request: Request, token_padre_jti: str | None = None) -> AsyncGenerator[str, None]:
    broadcaster = request.app.state.sse_broadcaster
    revocacion = request.app.state.token_revocation
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
            if token_padre_jti is not None and revocacion.contiene(token_padre_jti):
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
    request: Request, ticket: str, jwt_handler: JWTHandlerDep
) -> StreamingResponse:
    """RF-11: el dashboard consume actualizaciones en tiempo real vía SSE.

    Requiere un ticket efímero emitido en POST /api/auth/sse-ticket porque
    EventSource no puede enviar el header Authorization. El ticket es de UN
    SOLO USO: viaja como query param (puede quedar en logs de proxies), así
    que reutilizarlo se rechaza aunque no haya expirado.
    """
    try:
        datos_ticket = jwt_handler.validar_ticket_sse(ticket)
    except CredencialesInvalidasError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    if not request.app.state.sse_ticket_store.consumir(
        datos_ticket.jti, datos_ticket.exp.timestamp()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Ticket SSE ya utilizado"
        )

    # Un ticket cuya sesión ya se cerró entre la emisión y el uso no abre nada.
    if datos_ticket.token_padre_jti is not None and request.app.state.token_revocation.contiene(
        datos_ticket.token_padre_jti
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="La sesión fue cerrada"
        )

    return StreamingResponse(
        _event_stream(request, datos_ticket.token_padre_jti),
        media_type="text/event-stream",
    )
