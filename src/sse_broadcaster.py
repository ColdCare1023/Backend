"""Broadcaster de Server-Sent Events (SSE) para telemetría en tiempo real (HU-19).

Criterios de aceptación implementados:
  - Escenario 1 (clientes conectados): Despacho inmediato del evento JSON a todos los suscriptores activos.
  - Escenario 2 (sin clientes conectados): Despacho omitido eficientemente sin consumo de memoria ni impacto en la persistencia.
  - Escenario 3 (desconexión abrupta): Detección de desconexión del cliente, liberación inmediata de la cola y recursos asociados sin afectar a los demás suscriptores.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("coldcare.sse")


class SSEBroadcaster:
    """Administrador de suscripciones SSE en memoria con métricas de latencia."""

    def __init__(self, queue_maxsize: int = 100):
        self.queue_maxsize = queue_maxsize
        self._subscribers: dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()
        self.total_dispatched: int = 0
        self.last_latency_ms: float = 0.0

    async def subscribe(self, client_id: str) -> asyncio.Queue:
        """Registra un nuevo cliente SSE."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_maxsize)
        async with self._lock:
            self._subscribers[client_id] = queue
            logger.info("Cliente SSE conectado: %s (Total: %d)", client_id, len(self._subscribers))
        return queue

    async def unsubscribe(self, client_id: str):
        """Desconecta y libera recursos de un cliente SSE."""
        async with self._lock:
            if client_id in self._subscribers:
                del self._subscribers[client_id]
                logger.info("Cliente SSE desconectado: %s (Total: %d)", client_id, len(self._subscribers))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def broadcast(self, event_type: str, data: dict[str, Any], received_at: float | None = None) -> float:
        """Despacha evento a suscriptores. Retorna la latencia en milisegundos."""
        t_emision = time.time()
        latencia_ms = ((t_emision - received_at) * 1000.0) if received_at else 0.0
        self.last_latency_ms = latencia_ms

        async with self._lock:
            if not self._subscribers:
                # Escenario 2: Sin clientes conectados, se omite el despacho para ahorrar recursos
                return latencia_ms

            payload_data = dict(data)
            payload_data["_sse_latency_ms"] = round(latencia_ms, 2)
            payload_data["_emitted_at"] = datetime.now(timezone.utc).isoformat()

            mensaje = f"event: {event_type}\ndata: {json.dumps(payload_data)}\n\n"

            clientes_a_remover = []
            for cid, queue in self._subscribers.items():
                try:
                    queue.put_nowait(mensaje)
                    self.total_dispatched += 1
                except asyncio.QueueFull:
                    logger.warning("Cola llena para cliente SSE %s, desconectando...", cid)
                    clientes_a_remover.append(cid)

            for cid in clientes_a_remover:
                if cid in self._subscribers:
                    del self._subscribers[cid]

        return latencia_ms


broadcaster_global = SSEBroadcaster()
sse_router = APIRouter(prefix="/api/v1/sse", tags=["SSE"])


@sse_router.get("/telemetria")
async def sse_telemetria_endpoint(request: Request):
    """Canal Server-Sent Events para telemetría en tiempo real."""
    client_id = f"client_{request.client.host if request.client else 'anon'}_{time.time_ns()}"
    queue = await broadcaster_global.subscribe(client_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        # Enviar evento inicial de conexión confirmada
        yield f"event: conectado\ndata: {json.dumps({'client_id': client_id, 'status': 'connected'})}\n\n"
        try:
            while True:
                # Si el cliente cerró la conexión HTTP
                if await request.is_disconnected():
                    break
                try:
                    mensaje = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield mensaje
                except asyncio.TimeoutError:
                    # Enviar ping/keepalive para mantener el canal abierto
                    yield ": ping\n\n"
        finally:
            await broadcaster_global.unsubscribe(client_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
