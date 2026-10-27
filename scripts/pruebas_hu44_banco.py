"""HU-44 / HU-10: pruebas de banco de la rotación y revocación de credenciales.

Ejecuta contra una pila real (EMQX local de `infra-local` o EMQX Cloud
Serverless, backend y PostgreSQL) los pasos que el nodo físico no hace solo, y
deja un registro con fecha y hora para `docs/evidencias/HU-44_evidencia.md`:

  atacante        D. Segundo cliente MQTT con el token VIEJO: publica una lectura
                  y espera acuse. Durante la retención no debe llegar acuse; tras
                  cambiar la consola, el broker debe rechazar la conexión (CONNACK 4/5).
  token-invalido  G. Conexión con un token aleatorio: CONNACK 4/5 (HU-10 esc. 2).
  acl             G. Con el token válido de un nodo, publica en el tópico de OTRO nodo.
                  El broker no debe entregarlo (HU-10 esc. 3; ver log de EMQX).
  confirmar       B'/E. Confirma la consola en el backend y espera la prueba de
                  posesión. Con la consola cambiada: CREDENCIAL_POSESION_VERIFICADA
                  (y el tiempo que tardó). Sin cambiarla: PRUEBA_POSESION_FALLIDA
                  con motivo firma_invalida, y la retención sigue.
  revocar         F. Revoca la credencial y publica con ella: acuse `rejected`
                  (modo manual, usuario aún en el broker) o CONNACK 4/5.
  informe         Bitácora y estado del dispositivo desde una hora dada (CSV).

La prueba C (excursión durante la retención) necesita el nodo físico: sonda en
hielo o junto a una fuente de calor durante la ventana. Después, `informe`
muestra EXCURSION_NO_VERIFICADA_DETECTADA con `captura_a_alerta_s`.

Uso (los tokens nunca se pasan por argumento: quedarían en el historial):
    export BANCO_API_URL=http://localhost:8000
    export BANCO_ADMIN_EMAIL=admin@farmacia.demo.pe BANCO_ADMIN_PASSWORD=...
    export BANCO_MQTT_HOST=localhost BANCO_MQTT_PORT=8883 BANCO_MQTT_CA=../infra-local/certs/ca.pem
    export BANCO_TOKEN_VIEJO=...      # atacante / revocar
    export BANCO_TOKEN_VALIDO=...     # acl
    python -m scripts.pruebas_hu44_banco atacante --device-id ESP32_FARMACIA_01
    python -m scripts.pruebas_hu44_banco confirmar --device-id ESP32_FARMACIA_01 --espera 180
    python -m scripts.pruebas_hu44_banco informe --device-id ESP32_FARMACIA_01 --desde 2026-10-28T15:00:00Z \\
        --salida docs/evidencias/logs/2026-10-28_hu44_informe.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import secrets
import ssl
import sys
import time
from datetime import datetime, timezone

import aiomqtt
import httpx

RECHAZO_CREDENCIAL = {4, 5, 134, 135}  # MQTT 3.1.1 (4, 5) y MQTT 5 (0x86, 0x87)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _log(paso: str, mensaje: str) -> None:
    print(f"{_ahora()}  [{paso}] {mensaje}", flush=True)


def _entorno(nombre: str, obligatorio: bool = True) -> str:
    valor = os.getenv(nombre, "")
    if obligatorio and not valor:
        sys.exit(f"Falta la variable de entorno {nombre}.")
    return valor


def _tls() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=_entorno("BANCO_MQTT_CA", obligatorio=False) or None)


def _cliente(usuario: str, token: str, identificador: str) -> aiomqtt.Client:
    return aiomqtt.Client(
        hostname=_entorno("BANCO_MQTT_HOST"),
        port=int(_entorno("BANCO_MQTT_PORT", obligatorio=False) or 8883),
        username=usuario,
        password=token,
        identifier=identificador,
        tls_context=_tls(),
        keepalive=30,
        timeout=15,
    )


def _telemetria(device_id: str, temperatura: float, seq_no: int) -> str:
    """Contrato v1.0 con boot_id propio (999999) para no chocar con el nodo real."""
    return json.dumps({
        "schema_version": "1.0", "device_id": device_id, "boot_id": 999999, "seq_no": seq_no,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "time_quality": "sntp_synced",
        "uptime_s": 1, "temp_ds18b20": temperatura, "ds18b20_status": "OK", "ds18b20_fault": None,
        "temp_sht31": 20.0, "humedad_sht31": 50.0, "sht31_status": "OK", "sht31_fault": None,
        "door_open": None, "door_open_s": None, "door_open_streak_s": None, "mc38_status": "not_installed",
        "power_source": None, "sensor_fault": False, "firmware_version": "v0.0.0",
    }, separators=(",", ":"))


def _codigo(exc: aiomqtt.MqttError) -> int | None:
    rc = getattr(exc, "rc", None)
    return getattr(rc, "value", rc)


async def _publicar_y_esperar_acuse(
    device_id: str, token: str, identificador: str, topico: str, cuerpo: str, espera: float, paso: str
) -> None:
    try:
        async with _cliente(device_id, token, identificador) as cliente:
            _log(paso, f"CONNACK 0: el broker aceptó la conexión como {device_id} (client id {identificador}).")
            await cliente.subscribe(f"farmacias/{device_id}/ack", qos=1)
            await cliente.publish(topico, cuerpo, qos=1)
            _log(paso, f"PUBACK recibido para {topico}. Espero acuse lógico {espera:.0f} s…")
            try:
                async with asyncio.timeout(espera):
                    async for mensaje in cliente.messages:
                        datos = json.loads(mensaje.payload)
                        if datos.get("boot_id") == 999999 or datos.get("tipo") == "desafio_posesion":
                            _log(paso, f"Mensaje en .../ack: {json.dumps(datos, ensure_ascii=False)}")
                            if datos.get("boot_id") == 999999:
                                return
            except TimeoutError:
                _log(paso, "Sin acuse lógico: la lectura no se persistió (esperado durante la retención).")
    except aiomqtt.MqttCodeError as exc:
        codigo = _codigo(exc)
        estado = "rechazo por credencial (esperado)" if codigo in RECHAZO_CREDENCIAL else "otro error"
        _log(paso, f"CONNACK {codigo}: {estado} — {exc}")
    except aiomqtt.MqttError as exc:
        _log(paso, f"Error MQTT: {exc}")


async def atacante(args: argparse.Namespace) -> None:
    await _publicar_y_esperar_acuse(
        args.device_id, _entorno("BANCO_TOKEN_VIEJO"), args.client_id or args.device_id,
        f"farmacias/{args.device_id}/lecturas", _telemetria(args.device_id, args.temperatura, int(time.time()) % 1_000_000 + 1),
        args.espera, "D",
    )


async def token_invalido(args: argparse.Namespace) -> None:
    await _publicar_y_esperar_acuse(
        args.device_id, secrets.token_urlsafe(32), f"{args.device_id}-banco",
        f"farmacias/{args.device_id}/lecturas", _telemetria(args.device_id, 4.0, 1), 5, "G-token",
    )


async def acl(args: argparse.Namespace) -> None:
    try:
        async with _cliente(args.device_id, _entorno("BANCO_TOKEN_VALIDO"), f"{args.device_id}-banco") as cliente:
            _log("G-acl", f"Conectado como {args.device_id}. Publico en farmacias/{args.otro}/lecturas…")
            await cliente.publish(f"farmacias/{args.otro}/lecturas", _telemetria(args.otro, 4.0, 1), qos=1)
            _log("G-acl", "El broker respondió la publicación. Compruebe en el log de EMQX "
                          "'authorization_permission_denied' y que el backend no registró nada de "
                          f"{args.otro}.")
    except aiomqtt.MqttError as exc:
        _log("G-acl", f"El broker cortó la publicación ajena: {exc} (esperado si deny_action = disconnect).")


async def _api() -> httpx.AsyncClient:
    base = _entorno("BANCO_API_URL", obligatorio=False) or "http://localhost:8000"
    cliente = httpx.AsyncClient(base_url=base, timeout=20)
    respuesta = await cliente.post(
        "/api/auth/login", json={"email": _entorno("BANCO_ADMIN_EMAIL"), "password": _entorno("BANCO_ADMIN_PASSWORD")}
    )
    respuesta.raise_for_status()
    cliente.headers["Authorization"] = f"Bearer {respuesta.json()['access_token']}"
    return cliente


async def _auditoria(api: httpx.AsyncClient, desde: str, accion: str | None = None) -> list[dict]:
    params = {"desde": desde, "limite": 1000}
    if accion:
        params["accion"] = accion
    respuesta = await api.get("/api/auditoria", params=params)
    respuesta.raise_for_status()
    return respuesta.json()


async def confirmar(args: argparse.Namespace) -> None:
    api = await _api()
    inicio = datetime.now(timezone.utc)
    marca = time.monotonic()
    respuesta = await api.post(f"/api/dispositivos/{args.device_id}/broker/confirmar", json={"nota": args.nota})
    _log("B'/E", f"Confirmar consola → HTTP {respuesta.status_code}; posesion_pendiente="
                 f"{respuesta.json().get('posesion_pendiente') if respuesta.is_success else '—'}")
    while time.monotonic() - marca < args.espera:
        filas = await _auditoria(api, inicio.isoformat())
        for fila in filas:
            if fila["accion"] in ("CREDENCIAL_POSESION_VERIFICADA", "PRUEBA_POSESION_FALLIDA"):
                tardanza = (datetime.fromisoformat(fila["created_at"]) - inicio).total_seconds()
                _log("B'/E", f"{fila['accion']} {json.dumps(fila['detalle'])} a {tardanza:.1f} s de confirmar.")
                if fila["accion"] == "CREDENCIAL_POSESION_VERIFICADA":
                    await api.aclose()
                    return
        await asyncio.sleep(5)
    _log("B'/E", f"Sin prueba de posesión verificada en {args.espera:.0f} s: la retención sigue.")
    await api.aclose()


async def revocar(args: argparse.Namespace) -> None:
    api = await _api()
    respuesta = await api.post(f"/api/dispositivos/{args.device_id}/credenciales/revocar", json={"motivo": args.motivo})
    _log("F", f"Revocar → HTTP {respuesta.status_code}")
    await api.aclose()
    await _publicar_y_esperar_acuse(
        args.device_id, _entorno("BANCO_TOKEN_VIEJO"), f"{args.device_id}-banco",
        f"farmacias/{args.device_id}/lecturas", _telemetria(args.device_id, 4.0, int(time.time()) % 1_000_000 + 1),
        args.espera, "F",
    )


async def informe(args: argparse.Namespace) -> None:
    api = await _api()
    filas = [f for f in await _auditoria(api, args.desde)
             if args.device_id in (f.get("recurso") or "") or args.device_id in json.dumps(f.get("detalle") or {})]
    estado = (await api.get("/api/dispositivos/estado-operativo", params={"todos": True})).json()
    await api.aclose()
    filas.sort(key=lambda f: f["created_at"])
    salida = open(args.salida, "w", newline="", encoding="utf-8") if args.salida else sys.stdout
    escritor = csv.writer(salida)
    escritor.writerow(["created_at", "accion", "recurso", "detalle"])
    for fila in filas:
        escritor.writerow([fila["created_at"], fila["accion"], fila["recurso"], json.dumps(fila["detalle"], ensure_ascii=False)])
    nodo = next((e for e in estado if e.get("id") == args.device_id), {})
    escritor.writerow([])
    escritor.writerow(["estado_operativo", nodo.get("estado_operativo"), "posesion_pendiente", nodo.get("posesion_pendiente")])
    for incidente in nodo.get("incidencias_operativas", []):
        escritor.writerow(["incidente", incidente["tipo"], incidente["estado"], json.dumps(incidente.get("detalle"))])
    if args.salida:
        salida.close()
        _log("informe", f"{len(filas)} filas en {args.salida}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="prueba", required=True)
    p = sub.add_parser("atacante")
    p.add_argument("--device-id", required=True)
    p.add_argument("--client-id", help="Por defecto el del nodo: provoca la expulsión mutua")
    p.add_argument("--temperatura", type=float, default=4.0)
    p.add_argument("--espera", type=float, default=15)
    p = sub.add_parser("token-invalido")
    p.add_argument("--device-id", required=True)
    p = sub.add_parser("acl")
    p.add_argument("--device-id", required=True)
    p.add_argument("--otro", required=True, help="device_id ajeno")
    p = sub.add_parser("confirmar")
    p.add_argument("--device-id", required=True)
    p.add_argument("--nota", default="prueba de banco")
    p.add_argument("--espera", type=float, default=180)
    p = sub.add_parser("revocar")
    p.add_argument("--device-id", required=True)
    p.add_argument("--motivo", default="prueba de banco F")
    p.add_argument("--espera", type=float, default=15)
    p = sub.add_parser("informe")
    p.add_argument("--device-id", required=True)
    p.add_argument("--desde", required=True, help="ISO 8601, p. ej. 2026-10-28T15:00:00Z")
    p.add_argument("--salida")
    args = parser.parse_args(argv)
    funciones = {
        "atacante": atacante, "token-invalido": token_invalido, "acl": acl,
        "confirmar": confirmar, "revocar": revocar, "informe": informe,
    }
    asyncio.run(funciones[args.prueba](args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
