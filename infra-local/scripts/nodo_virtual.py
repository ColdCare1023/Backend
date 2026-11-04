"""Nodo virtual: prueba la cadena completa nodo → EMQX → backend → acuse → API sin un ESP32.

Los bytes que se publican y la decisión sobre cada acuse los produce el código REAL del
firmware (`src/core` del repo ColdCare1023/IoT) compilado como `tools/nodo_core.cpp`. Este
script solo pone la radio: la misma sesión MQTT que `MQTTManager` del ESP32 (client_id y
usuario = device_id, contraseña = token, TLS 8883 validando CA y host, LWT, QoS 1,
suscripción a `ack` y `ota`).

Uso (desde infra-local/, con la pila arriba y el dispositivo dado de alta):
    python scripts/crear_dispositivo.py ESP32_FARMACIA_01
    python scripts/nodo_virtual.py ESP32_FARMACIA_01 [--host IP] [--lecturas 3] [--aceptar-privacidad]

Comprueba: CONNECT y SUBSCRIBE, evento `online`, N lecturas (la última fuera de 2-8 °C)
con acuse que el firmware libera (huella IOT-008 incluida), reenvío duplicado, evento
`estado_nodo`, que la API devuelve las lecturas (lo que muestra el dashboard) y que un corte
abrupto deja el nodo `offline` por el LWT. La consulta a la API usa el usuario técnico de
`seed_dev`; si aún no aceptó la privacidad (BE-014) se omite, salvo `--aceptar-privacidad`.

`nodo_core` se busca en NODO_CORE o en <FW_DIR>/.pio/gen/ (FW_DIR: ../../IoT). Compilarlo
como indica la cabecera de tools/nodo_core.cpp. Requiere paho-mqtt (requirements.txt).
No imprime el token.
"""

import argparse
import json
import os
import queue
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import paho.mqtt.client as mqtt

RAIZ = Path(__file__).resolve().parents[1]
API = os.environ.get("API_URL", "http://localhost:8000")
ACUSE_TIMEOUT_S = 8  # ACK_LOGICO_TIMEOUT_MS del firmware


def localizar_core() -> str:
    if os.environ.get("NODO_CORE"):
        return os.environ["NODO_CORE"]
    gen = (RAIZ / os.environ.get("FW_DIR", "../../IoT")).resolve() / ".pio" / "gen"
    for nombre in ("nodo_core.exe", "nodo_core"):
        if (gen / nombre).exists():
            return str(gen / nombre)
    raise SystemExit(f"No se encontró nodo_core en {gen}: compílelo (cabecera de tools/nodo_core.cpp).")


class Nodo:
    def __init__(self, core: str, device_id: str, token: str, boot_id: int) -> None:
        self.core, self.device_id, self.token, self.boot_id = core, device_id, token, boot_id
        self.inicio = time.monotonic()
        self.acuses: queue.Queue[str] = queue.Queue()
        self.conexion: queue.Queue[object] = queue.Queue()
        self.cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=device_id, protocol=mqtt.MQTTv311, clean_session=True
        )

    def nucleo(self, *args: str, entrada: str | None = None) -> str:
        r = subprocess.run(
            [self.core, *args], input=entrada, capture_output=True, text=True, encoding="utf-8", check=False
        )
        if r.returncode != 0:
            raise RuntimeError(f"nodo_core {args[0]}: {r.stderr.strip()}")
        return r.stdout

    def uptime(self) -> str:
        return str(30 + int(time.monotonic() - self.inicio))

    def topico(self, sufijo: str) -> str:
        return f"farmacias/{self.device_id}/{sufijo}"

    def conectar(self, host: str, ca: Path) -> object:
        self.cliente.username_pw_set(self.device_id, self.token)
        self.cliente.tls_set_context(ssl.create_default_context(cafile=str(ca)))  # CA + nombre de host
        self.cliente.will_set(self.topico("eventos"), self.nucleo("lwt", self.device_id, str(self.boot_id)), qos=1)
        self.cliente.on_connect = lambda _c, _u, _f, rc, _p=None: self.conexion.put(rc)
        self.cliente.on_message = lambda _c, _u, msg: self.acuses.put(msg.payload.decode("utf-8", "replace"))
        self.cliente.connect(host, 8883, keepalive=60)
        self.cliente.loop_start()
        return self.conexion.get(timeout=15)

    def publicar(self, sufijo: str, cuerpo: str) -> bool:
        info = self.cliente.publish(self.topico(sufijo), cuerpo, qos=1)
        info.wait_for_publish(5)
        return info.is_published()

    def lectura(self, seq: int, temp_centi: int) -> str:
        """Publica una lectura y devuelve lo que el firmware haría con el acuse."""
        cuerpo = self.nucleo(
            "tele", self.device_id, str(self.boot_id), str(seq), str(int(time.time())), self.uptime(), str(temp_centi)
        )
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", newline="", delete=False) as f:
            f.write(cuerpo)
        try:
            if not self.publicar("lecturas", cuerpo):
                return "SIN_PUBACK"
            fin = time.monotonic() + ACUSE_TIMEOUT_S
            while (resta := fin - time.monotonic()) > 0:
                try:
                    acuse = self.acuses.get(timeout=resta)
                except queue.Empty:
                    break
                decision = self.nucleo("ack", self.device_id, str(self.boot_id), str(seq), f.name, entrada=acuse)
                if not decision.startswith("IGNORADO"):
                    return decision
            return "SIN_ACUSE (CONSERVAR)"
        finally:
            os.unlink(f.name)

    def cortar(self) -> None:
        """Corte sin DISCONNECT (como un corte de energía): el broker publica el LWT."""
        socket = self.cliente.socket()
        if socket is not None:
            socket.close()
        self.cliente.loop_stop()


def api(ruta: str, token: str | None = None, formulario: dict | None = None):
    cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
    datos = None
    if formulario is not None:
        datos = urllib.parse.urlencode(formulario).encode()
        cabeceras["Content-Type"] = "application/x-www-form-urlencoded"
    with urllib.request.urlopen(urllib.request.Request(API + ruta, data=datos, headers=cabeceras), timeout=15) as r:
        return json.loads(r.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("device_id", nargs="?", default="ESP32_FARMACIA_01")
    parser.add_argument("--host", help="host del broker (defecto: BROKER_IP de .env, como el nodo real)")
    parser.add_argument("--lecturas", type=int, default=3)
    parser.add_argument("--aceptar-privacidad", action="store_true", help="aceptarla para el usuario técnico semilla")
    args = parser.parse_args()

    env = dict(linea.split("=", 1) for linea in (RAIZ / ".env").read_text().splitlines() if "=" in linea)
    archivo_token = RAIZ / "secrets" / f"{args.device_id}.token"
    if not archivo_token.exists():
        raise SystemExit(f"Falta {archivo_token}: dé de alta el nodo con scripts/crear_dispositivo.py.")
    nodo = Nodo(localizar_core(), args.device_id, archivo_token.read_text().strip(), int(time.time()) % 1_000_000)
    host = args.host or env.get("BROKER_IP", "localhost").strip()

    resultados: list[bool] = []

    def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
        resultados.append(ok)
        print(f"[{'OK ' if ok else 'FALLA'}] {nombre}  {detalle}".rstrip(), flush=True)

    rc = nodo.conectar(host, RAIZ / "certs" / "ca.crt")
    comprobar(f"CONNECT TLS {host}:8883 como {args.device_id}", rc == 0, f"rc={rc}")
    if rc != 0:
        return 1
    for sufijo in ("ack", "ota"):
        comprobar(f"SUBSCRIBE {sufijo}", nodo.cliente.subscribe(nodo.topico(sufijo), qos=1)[0] == mqtt.MQTT_ERR_SUCCESS)
    time.sleep(0.5)
    comprobar(
        "evento online",
        nodo.publicar("eventos", nodo.nucleo("online", args.device_id, str(nodo.boot_id), str(int(time.time())),
                                             nodo.uptime(), "0")),
    )

    temperaturas = [425 + 5 * i for i in range(max(args.lecturas - 1, 0))] + [912]
    for seq, centi in enumerate(temperaturas[: args.lecturas], start=1):
        decision = nodo.lectura(seq, centi)
        comprobar(f"lectura seq={seq} {centi / 100:.2f} °C → el firmware la libera", decision.startswith("LIBERAR"),
                  decision)
    decision = nodo.lectura(1, temperaturas[0])
    comprobar("reenvío de la seq=1 (acuse perdido) → duplicado, se libera", decision.startswith("LIBERAR"), decision)

    detalle = json.dumps({"pendientes": 0, "perdidas_total": 0, "rssi_dbm": -61, "intervalo_s": 30})
    comprobar(
        "evento estado_nodo",
        nodo.publicar("eventos", nodo.nucleo("evento", args.device_id, str(nodo.boot_id), "estado_nodo",
                                             str(int(time.time())), nodo.uptime(), detalle)),
    )
    nodo.cortar()

    clave = env.get("SEED_TECNICO_PASSWORD", "").strip()
    if not clave:
        print("[--- ] API omitida: no hay SEED_TECNICO_PASSWORD en .env (scripts.seed_dev).")
    else:
        token = api("/api/auth/login", formulario={"username": "tecnico@farmacia.demo.pe", "password": clave})[
            "access_token"
        ]
        try:
            lecturas = api(f"/api/lecturas?device_id={args.device_id}", token)
        except urllib.error.HTTPError as error:
            if error.code != 403 or not args.aceptar_privacidad:
                print(f"[--- ] API omitida: HTTP {error.code} (¿privacidad sin aceptar? use --aceptar-privacidad).")
                lecturas = None
            else:
                api("/api/auth/privacidad/aceptar", token, formulario={})
                lecturas = api(f"/api/lecturas?device_id={args.device_id}", token)
        if lecturas is not None:
            mias = sorted(
                (lectura for lectura in lecturas
                 if str(lectura.get("reading_id", "")).startswith(f"{args.device_id}_{nodo.boot_id}_")),
                key=lambda lectura: lectura["reading_id"],
            )
            comprobar(f"API /api/lecturas: las {args.lecturas} lecturas, sin duplicar", len(mias) == args.lecturas,
                      f"{[lectura.get('temperatura_interna') for lectura in mias]}")
            estado = None
            for _ in range(10):  # el LWT llega en cuanto el broker ve caer la conexión
                estados = api("/api/dispositivos/estado-operativo?todos=true", token)
                estado = next((e for e in estados if e.get("id") == args.device_id), None)
                if estado and estado.get("estado_conectividad") == "offline":
                    break
                time.sleep(1)
            comprobar("LWT tras corte abrupto → nodo offline en la API",
                      bool(estado) and estado.get("estado_conectividad") == "offline",
                      str(estado and estado.get("estado_conectividad")))

    fallas = resultados.count(False)
    print(f"\n{len(resultados) - fallas}/{len(resultados)} comprobaciones OK")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
