"""ALE-02: servidor SMTP de prueba que FALLA a propósito.

Modos (`--modo`):
  rechazar    responde 554 al conectar (rechazo inmediato)
  timeout     acepta la conexión y no responde (agota el tiempo del cliente)
  temporal    responde 451 a RCPT TO (error 4xx: reintentable)
  permanente  responde 550 a RCPT TO (error 5xx: no reintentable)
  ok          acepta y descarta el correo (control)

Uso (el backend con SMTP_SEGURIDAD=ninguna):
    python -m scripts.smtp_falla --modo temporal --puerto 1025
    SMTP_ENABLED=true EMAIL_ENABLED=true SMTP_HOST=127.0.0.1 SMTP_PORT=1025 SMTP_SEGURIDAD=ninguna ...
"""

from __future__ import annotations

import argparse
import asyncio

MODOS = ("rechazar", "timeout", "temporal", "permanente", "ok")


async def atender(modo: str, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
    async def decir(linea: str) -> None:
        escritor.write((linea + "\r\n").encode())
        await escritor.drain()

    try:
        if modo == "rechazar":
            await decir("554 5.3.2 Servicio no disponible")
            return
        if modo == "timeout":
            await lector.read()  # no responde nada; termina cuando el cliente se cansa y cierra
            return
        await decir("220 smtp-falla listo")
        while True:
            linea = (await lector.readline()).decode(errors="replace").strip()
            if not linea:
                return
            orden = linea.split(" ", 1)[0].upper()
            if orden in ("EHLO", "HELO"):
                await decir("250 smtp-falla")
            elif orden == "RCPT" and modo == "temporal":
                await decir("451 4.3.0 Intente más tarde")
            elif orden == "RCPT" and modo == "permanente":
                await decir("550 5.1.1 Destinatario rechazado")
            elif orden == "DATA":
                await decir("354 Fin con <CRLF>.<CRLF>")
                while (await lector.readline()).strip() != b".":
                    pass
                await decir("250 2.0.0 Aceptado")
            elif orden == "QUIT":
                await decir("221 Adiós")
                return
            else:
                await decir("250 OK")
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        escritor.close()


async def servir(modo: str, host: str, puerto: int) -> asyncio.AbstractServer:
    return await asyncio.start_server(lambda r, w: atender(modo, r, w), host, puerto)


async def _main(modo: str, host: str, puerto: int) -> None:
    servidor = await servir(modo, host, puerto)
    print(f"SMTP de prueba en {host}:{puerto}, modo={modo}", flush=True)
    async with servidor:
        await servidor.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modo", choices=MODOS, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--puerto", type=int, default=1025)
    argumentos = parser.parse_args()
    asyncio.run(_main(argumentos.modo, argumentos.host, argumentos.puerto))
