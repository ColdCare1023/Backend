import asyncio

from src.interface.api.api_protection import LimiteCuerpoASGI


def test_limite_asgi_rechaza_cuerpo_chunked_sin_content_length():
    async def app(scope, receive, send):
        await receive()

    mensajes = [{"type": "http.request", "body": b"x" * 11, "more_body": False}]
    enviados = []

    async def receive():
        return mensajes.pop(0)

    async def send(message):
        enviados.append(message)

    asyncio.run(
        LimiteCuerpoASGI(app, max_bytes=10)(
            {"type": "http", "method": "POST", "path": "/api/lecturas"}, receive, send
        )
    )
    assert enviados[0]["type"] == "http.response.start"
    assert enviados[0]["status"] == 413
