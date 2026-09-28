"""Simulador de backend para el banco de pruebas del nodo (HU-07, HU-11, HU-15).

Hace de "FastAPI + PostgreSQL" mínimo: se suscribe a farmacias/+/lecturas,
valida cada mensaje con el esquema Pydantic v1.0, lo "persiste" en SQLite con
UNIQUE(device_id, boot_id, seq_no) y publica el acuse lógico en
farmacias/<device_id>/ack. Permite probar el firmware SIN depender del backend
real y forzar los casos difíciles:

  --modo normal        committed / duplicate / rejected según corresponda
  --modo sin_ack       nunca responde → el nodo debe CONSERVAR todo en LittleFS
                       aunque reciba PUBACK (regla de oro)
  --modo rechazar      responde rejected → el nodo debe mover a cuarentena
  --modo intermitente  responde solo a una fracción (--prob) → reanudación FIFO
  --modo lento         responde tras --retraso s (> 8 s = timeout del nodo)

NO ejecutar contra el broker de producción mientras el backend real está
activo: habría dos acuses por lectura.

Uso (banco local, ver README.md de esta carpeta):
    python simulador_backend_ack.py --host 192.168.1.50 --ca certs/ca.crt \\
        --usuario backend_qa --token ****** --modo normal --db banco.sqlite
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "contrato"))
from pydantic import ValidationError  # noqa: E402
from telemetria_v1 import parsear_telemetria  # noqa: E402


class Almacen:
    """Persistencia mínima con la misma restricción de idempotencia que HU-11."""

    def __init__(self, ruta: str):
        self._db = sqlite3.connect(ruta, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS lecturas (device_id TEXT, boot_id INTEGER, seq_no INTEGER, "
            "captured_at TEXT, received_at TEXT, payload TEXT, UNIQUE(device_id, boot_id, seq_no))")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS conflictos (device_id TEXT, boot_id INTEGER, seq_no INTEGER, "
            "recibido TEXT, payload_nuevo TEXT)")
        self._db.commit()

    def guardar(self, t, crudo: str) -> str:
        """Devuelve 'committed' o 'duplicate'. Conflicto de payload → se audita."""
        with self._lock:
            ahora = datetime.now(timezone.utc).isoformat()
            try:
                self._db.execute("INSERT INTO lecturas VALUES (?,?,?,?,?,?)",
                                 (t.device_id, t.boot_id, t.seq_no,
                                  t.captured_at.isoformat() if t.captured_at else None, ahora, crudo))
                self._db.commit()  # el acuse se publica DESPUÉS de este COMMIT
                return "committed"
            except sqlite3.IntegrityError:
                previo = self._db.execute(
                    "SELECT payload FROM lecturas WHERE device_id=? AND boot_id=? AND seq_no=?",
                    (t.device_id, t.boot_id, t.seq_no)).fetchone()[0]
                if json.loads(previo) != json.loads(crudo):
                    self._db.execute("INSERT INTO conflictos VALUES (?,?,?,?,?)",
                                     (t.device_id, t.boot_id, t.seq_no, ahora, crudo))
                    self._db.commit()
                return "duplicate"

    def resumen(self) -> str:
        n = self._db.execute("SELECT COUNT(*) FROM lecturas").fetchone()[0]
        c = self._db.execute("SELECT COUNT(*) FROM conflictos").fetchone()[0]
        return f"{n} lecturas persistidas, {c} conflictos auditados"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=os.environ.get("BANCO_HOST", "localhost"))
    p.add_argument("--puerto", type=int, default=8883)
    p.add_argument("--ca", required=True, help="CA que firmó el certificado del broker")
    p.add_argument("--usuario", default=os.environ.get("BANCO_USER", "backend_qa"))
    p.add_argument("--token", default=os.environ.get("BANCO_TOKEN", ""))
    p.add_argument("--modo", choices=["normal", "sin_ack", "rechazar", "intermitente", "lento"], default="normal")
    p.add_argument("--prob", type=float, default=0.5, help="fracción acusada en modo intermitente")
    p.add_argument("--retraso", type=float, default=12.0, help="segundos de retraso en modo lento")
    p.add_argument("--db", default=":memory:")
    p.add_argument("--duracion", type=float, default=0, help="segundos antes de salir (0 = hasta Ctrl+C)")
    a = p.parse_args(argv)
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")

    import paho.mqtt.client as mqtt

    almacen = Almacen(a.db)
    rng = random.Random(2026)
    contadores = {"recibidos": 0, "invalidos": 0, "acuses": 0}

    def acusar(cliente, device_id: str, boot_id: int, seq_no: int, estado: str, motivo: str | None = None):
        cuerpo = {"device_id": device_id, "boot_id": boot_id, "seq_no": seq_no, "status": estado}
        if motivo:
            cuerpo["reason"] = motivo[:120]
        cliente.publish(f"farmacias/{device_id}/ack", json.dumps(cuerpo), qos=1)
        contadores["acuses"] += 1

    def al_mensaje(cliente, _u, msg):
        contadores["recibidos"] += 1
        hora = datetime.now().strftime("%H:%M:%S")
        try:
            t = parsear_telemetria(msg.payload)
        except (ValidationError, ValueError) as e:
            contadores["invalidos"] += 1
            try:
                d = json.loads(msg.payload)
                ident = (str(d["device_id"]), int(d["boot_id"]), int(d["seq_no"]))
            except (ValueError, KeyError, TypeError):
                print(f"{hora} ✗ mensaje ilegible en {msg.topic}: descartado sin acuse")
                return
            print(f"{hora} ✗ {ident} no cumple el contrato → rejected ({str(e).splitlines()[0]})")
            acusar(cliente, *ident, "rejected", "schema")
            return

        ident = (t.device_id, t.boot_id, t.seq_no)
        temp = "falla" if t.temp_ds18b20 is None else f"{t.temp_ds18b20:.2f} °C"
        sim = " [SIMULADA]" if t.simulated else ""
        if a.modo == "rechazar":
            print(f"{hora} → {ident} {temp}{sim}: rejected (modo rechazar)")
            acusar(cliente, *ident, "rejected", "modo_rechazar")
            return
        estado = almacen.guardar(t, msg.payload.decode())
        if a.modo == "sin_ack" or (a.modo == "intermitente" and rng.random() > a.prob):
            print(f"{hora} · {ident} {temp}{sim}: {estado} en BD, SIN acuse (modo {a.modo})")
            return
        if a.modo == "lento":
            print(f"{hora} … {ident} {temp}{sim}: {estado}, acuse en {a.retraso:.0f} s")
            threading.Timer(a.retraso, acusar, (cliente, *ident, estado)).start()
            return
        print(f"{hora} ✓ {ident} {temp}{sim}: {estado}")
        acusar(cliente, *ident, estado)

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"backend-qa-{os.getpid()}",
                          protocol=mqtt.MQTTv311)
    cliente.username_pw_set(a.usuario, a.token)
    cliente.tls_set(ca_certs=a.ca)
    cliente.on_message = al_mensaje
    def al_conectar(c, _u, _flags, codigo, _props=None):
        if codigo.is_failure:
            print(f"Conexión rechazada por el broker: {codigo}", file=sys.stderr)
            return
        c.subscribe("farmacias/+/lecturas", qos=1)

    cliente.on_connect = al_conectar
    cliente.connect(a.host, a.puerto, keepalive=30)
    print(f"Simulador de backend en modo '{a.modo}' escuchando farmacias/+/lecturas en {a.host}:{a.puerto} (TLS)")
    cliente.loop_start()
    try:
        inicio = time.time()
        while a.duracion <= 0 or time.time() - inicio < a.duracion:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        cliente.loop_stop()
        cliente.disconnect()
    print(f"\nRecibidos {contadores['recibidos']}, inválidos {contadores['invalidos']}, "
          f"acuses {contadores['acuses']}. {almacen.resumen()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
