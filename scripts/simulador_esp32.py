"""Simulador de dispositivo ESP32 — envía lecturas reales al backend sin hardware físico.

No es un mock ni una fake API: actúa como cliente HTTP externo que hace login real
y llama al endpoint real `POST /api/lecturas` con el mismo contrato que usaría un
ESP32 físico (ver LecturaIngestRequest en src/interface/api/schemas.py). El backend,
la IA, la trazabilidad y las alertas corren 100% real; solo se reemplaza el emisor.

Requisito: el device_id debe existir en la tabla `devices` (modo estricto, default)
o el backend debe correr con DEVICE_REGISTRY_ESTRICTO=false para auto-registrarlo
en la primera lectura.

Uso:
    python -m scripts.simulador_esp32
    python -m scripts.simulador_esp32 --device-id ESP32-002 --intervalo 5 --n 50
    python -m scripts.simulador_esp32 --base-url http://localhost:8000 --forzar-alerta

Variables de entorno (opcionales, tienen default):
    SIMULADOR_BASE_URL   (default: http://localhost:8000)
    SIMULADOR_EMAIL      (default: tecnico@farmacia.demo.pe)
    SIMULADOR_PASSWORD   (default: tecni12345)
    SIMULADOR_DEVICE_ID  (default: ESP32-001)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timezone

import httpx

BASE_URL_DEFAULT = os.getenv("SIMULADOR_BASE_URL", "http://localhost:8000")
EMAIL_DEFAULT = os.getenv("SIMULADOR_EMAIL", "tecnico@farmacia.demo.pe")
PASSWORD_DEFAULT = os.getenv("SIMULADOR_PASSWORD", "tecni12345")
DEVICE_ID_DEFAULT = os.getenv("SIMULADOR_DEVICE_ID", "ESP32-001")
# Debe coincidir con FIRMWARE_VERSION de iot-firmware/src/config.h.
FIRMWARE_VERSION_SIMULADA = os.getenv("SIMULADOR_FIRMWARE_VERSION", "1.0.0")

# Rango normal de cadena de frío farmacéutica: 2°C a 8°C.
TEMP_NORMAL_MIN = 2.0
TEMP_NORMAL_MAX = 8.0
HUMEDAD_MIN = 40.0
HUMEDAD_MAX = 65.0

# Fuera de rango, para forzar generación de alerta por la IA / reglas de negocio.
TEMP_ALERTA_MIN = 12.0
TEMP_ALERTA_MAX = 20.0


class SimuladorError(RuntimeError):
    pass


def _generar_lectura(device_id: str, forzar_alerta: bool) -> dict:
    if forzar_alerta:
        temp_ambiental = round(random.uniform(TEMP_ALERTA_MIN, TEMP_ALERTA_MAX), 2)
    else:
        temp_ambiental = round(random.uniform(TEMP_NORMAL_MIN, TEMP_NORMAL_MAX), 2)

    temp_interna = round(temp_ambiental + random.uniform(-0.5, 0.5), 2)
    humedad = round(random.uniform(HUMEDAD_MIN, HUMEDAD_MAX), 2)
    apertura = random.random() < 0.05  # 5% de probabilidad de puerta abierta

    # Mismos campos, mismo orden y mismos tipos que `PayloadBuilder::build()`
    # del firmware (ver IoT-documentacion_iot.md §3.5).
    #
    # Antes se omitían `firmware_version` y `duracion_apertura_segundos`, así
    # que este "simulador de ESP32" enviaba un payload que el ESP32 real nunca
    # produce. Por eso el contrato pudo romperse sin que nada lo delatara: el
    # backend rechazaba el 100% de las lecturas del nodo físico mientras el
    # simulador seguía en verde. Si el firmware cambia de forma, esto debe
    # cambiar con él.
    return {
        "device_id": device_id,
        "timestamp": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "estado_conectividad": "online",
        "firmware_version": FIRMWARE_VERSION_SIMULADA,
        "temperatura_interna": temp_interna,
        "temperatura_ambiental": temp_ambiental,
        "humedad_ambiental": humedad,
        "apertura_refrigerador": apertura,
        # El nodo lo emite siempre: 0 con la puerta cerrada.
        "duracion_apertura_segundos": random.randint(30, 300) if apertura else 0,
    }


async def _login(client: httpx.AsyncClient, base_url: str, email: str, password: str) -> str:
    resp = await client.post(
        f"{base_url}/api/auth/login",
        data={"username": email, "password": password},
    )
    if resp.status_code != 200:
        raise SimuladorError(
            f"Login falló ({resp.status_code}): {resp.text}\n"
            f"Verifica que el backend esté corriendo y que existan los usuarios demo "
            f"(python -m scripts.seed_dev)."
        )
    return resp.json()["access_token"]


async def _enviar_lectura(client: httpx.AsyncClient, base_url: str, token: str, payload: dict) -> httpx.Response:
    return await client.post(
        f"{base_url}/api/lecturas",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


async def ejecutar(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    print("── Simulador ESP32 ──────────────────────────────")
    print(f"Backend:    {base_url}")
    print(f"Device ID:  {args.device_id}")
    print(f"Intervalo:  {args.intervalo}s")
    print(f"Lecturas:   {'infinito' if args.n <= 0 else args.n}")
    print(f"Forzar alerta cada: {args.forzar_cada} lecturas" if args.forzar_alerta else "Forzar alerta: desactivado")
    print("──────────────────────────────────────────────────")

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            token = await _login(client, base_url, args.email, args.password)
        except httpx.ConnectError:
            print(f"✗ No se pudo conectar a {base_url}. ¿Está el backend corriendo?")
            return 1
        except SimuladorError as exc:
            print(f"✗ {exc}")
            return 1

        print(f"✓ Login OK como {args.email}")

        contador = 0
        while args.n <= 0 or contador < args.n:
            contador += 1
            forzar = args.forzar_alerta and contador % args.forzar_cada == 0
            payload = _generar_lectura(args.device_id, forzar)

            try:
                resp = await _enviar_lectura(client, base_url, token, payload)
            except httpx.ConnectError:
                print(f"[{contador}] ✗ Backend no responde, reintentando en {args.intervalo}s...")
                await asyncio.sleep(args.intervalo)
                continue

            etiqueta = "⚠ ALERTA" if forzar else "  normal"
            if resp.status_code == 201:
                cuerpo = resp.json()
                confianza = cuerpo.get("confianza_ia")
                riesgo = cuerpo.get("nivel_riesgo") or cuerpo.get("clasificacion_ia")
                print(
                    f"[{contador}] {etiqueta} → {payload['temperatura_ambiental']}°C "
                    f"| riesgo={riesgo} confianza={confianza} → 201 OK"
                )
            elif resp.status_code == 403:
                print(
                    f"[{contador}] ✗ 403 Dispositivo no autorizado. El device_id "
                    f"'{args.device_id}' no existe en la tabla devices.\n"
                    f"    Solución: registra el dispositivo o corre el backend con "
                    f"DEVICE_REGISTRY_ESTRICTO=false para auto-registrarlo."
                )
                return 1
            elif resp.status_code == 422:
                print(f"[{contador}] ✗ 422 Lectura inválida: {resp.json()}")
            elif resp.status_code == 401:
                print(f"[{contador}] ✗ 401 token expirado, re-logueando...")
                token = await _login(client, base_url, args.email, args.password)
                continue
            else:
                print(f"[{contador}] ✗ {resp.status_code}: {resp.text}")

            if args.n <= 0 or contador < args.n:
                await asyncio.sleep(args.intervalo)

    print("── Fin de la simulación ─────────────────────────")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=BASE_URL_DEFAULT, help="URL del backend (default: %(default)s)")
    parser.add_argument("--email", default=EMAIL_DEFAULT, help="Email de login (rol tecnico o farmaceutico)")
    parser.add_argument("--password", default=PASSWORD_DEFAULT, help="Password de login")
    parser.add_argument("--device-id", default=DEVICE_ID_DEFAULT, help="device_id a simular")
    parser.add_argument("--intervalo", type=float, default=10.0, help="Segundos entre lecturas (default: 10)")
    parser.add_argument("--n", type=int, default=0, help="Número de lecturas a enviar (0 = infinito, default: 0)")
    parser.add_argument(
        "--forzar-alerta",
        action="store_true",
        help="Genera lecturas fuera de rango periódicamente para probar alertas",
    )
    parser.add_argument(
        "--forzar-cada",
        type=int,
        default=5,
        help="Cada cuántas lecturas forzar una fuera de rango (default: 5, requiere --forzar-alerta)",
    )
    args = parser.parse_args()

    try:
        codigo = asyncio.run(ejecutar(args))
    except KeyboardInterrupt:
        print("\n── Simulación detenida por el usuario ───────────")
        codigo = 0
    sys.exit(codigo)


if __name__ == "__main__":
    main()
