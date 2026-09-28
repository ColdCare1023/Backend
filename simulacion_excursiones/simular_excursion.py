"""Inyección de excursiones SIMULADAS para la demo / sustentación.

En la botica real no se puede provocar una falla térmica sin arriesgar
medicamentos. Este script publica, por MQTT/TLS, lecturas con el contrato v1.0
que recorren un perfil térmico (p. ej. 4,8 → 12,5 → 4,9 °C) para demostrar en
vivo: el cambio de color del dashboard (SSE), el correo de alerta crítica
fuera de horario (HU-53) y el registro en la cadena SHA-256.

SALVAGUARDAS (no negociables)
  - Cada lectura lleva "simulated": true (el backend la excluye de KPI e IA).
  - Por defecto solo publica como un dispositivo cuyo device_id contiene
    "DEMO" (ESP32_DEMO_01). Publicar con la identidad de un nodo real
    contaminaría su cadena de trazabilidad: exige --permitir-device-no-demo.
  - Cada ejecución usa un boot_id propio (la hora Unix): nunca choca con la
    identidad (device_id, boot_id, seq_no) de otra corrida.
  - Cada payload se valida con el esquema Pydantic v1.0 antes de publicarse.

Uso:
    python simular_excursion.py --dry-run                       # solo muestra los mensajes
    set EMQX_HOST=xxxx.emqxsl.com & set EMQX_TOKEN=... & set EMQX_CA=emqxsl-ca.crt
    python simular_excursion.py --perfil excursion --intervalo 5

Perfiles: normal, excursion, preventivo, falla_sensor, puerta.
Requiere paho-mqtt ≥ 2.0 y pydantic ≥ 2 (salvo --dry-run, que solo usa pydantic).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "contrato"))
from telemetria_v1 import TelemetriaV1  # noqa: E402

PERFILES: dict[str, list[dict]] = {
    # Cruza 8 °C → excursión crítica por la regla determinista (HU-21) y vuelve.
    "excursion": [{"t": t} for t in (4.8, 5.2, 6.1, 7.4, 8.6, 10.3, 12.5, 12.5, 11.0, 8.9, 7.2, 5.5, 4.9)],
    # Subida lenta DENTRO de 2–8 °C: material para la clase riesgo_preventivo (HU-18/HU-20).
    "preventivo": [{"t": round(4.6 + 0.28 * i, 2)} for i in range(12)],
    # Falla de sonda en medio de lecturas normales: NO debe generar excursión (HU-21 esc. 4).
    "falla_sensor": [{"t": 4.7}, {"t": 4.8}, {"t": None, "falla": "crc_error"},
                     {"t": None, "falla": "no_response"}, {"t": None, "falla": "no_response"}, {"t": 4.9}, {"t": 4.8}],
    # Puerta abierta de forma prolongada (HU-04 esc. 3 / HU-35).
    "puerta": [{"t": 4.9, "puerta": 0}, {"t": 5.3, "puerta": 30}, {"t": 6.0, "puerta": 60, "racha": 60},
               {"t": 6.8, "puerta": 90, "racha": 90}, {"t": 7.3, "puerta": 120, "racha": 120}, {"t": 6.1, "puerta": 0}],
    "normal": [{"t": t} for t in (4.6, 4.7, 4.7, 4.8, 4.6, 4.5)],
}


def construir(device_id: str, boot_id: int, seq: int, paso: dict, uptime: int) -> dict:
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    ds_ok = paso.get("t") is not None
    puerta = paso.get("puerta")
    mensaje = {
        "schema_version": "1.0",
        "device_id": device_id,
        "boot_id": boot_id,
        "seq_no": seq,
        "captured_at": ahora.isoformat().replace("+00:00", "Z"),
        "time_quality": "sntp_synced",
        "uptime_s": uptime,
        "temp_ds18b20": paso["t"] if ds_ok else None,
        "ds18b20_status": "OK" if ds_ok else "SENSOR_FAULT",
        "ds18b20_fault": None if ds_ok else paso.get("falla", "no_response"),
        "temp_sht31": 22.4,
        "humedad_sht31": 58.0,
        "sht31_status": "OK",
        "sht31_fault": None,
        "door_open": bool(puerta) if puerta is not None else False,
        "door_open_s": min(puerta or 0, 30),
        "door_open_streak_s": paso.get("racha"),
        "mc38_status": "installed",
        "power_source": None,
        "sensor_fault": not ds_ok,
        "firmware_version": "v1.2.0",
        "simulated": True,
    }
    TelemetriaV1.model_validate(mensaje)  # nunca se publica algo fuera de contrato
    return mensaje


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--perfil", choices=sorted(PERFILES), default="excursion")
    p.add_argument("--device-id", default="ESP32_DEMO_01")
    p.add_argument("--intervalo", type=float, default=5.0, help="segundos entre lecturas (demo: 5)")
    p.add_argument("--host", default=os.environ.get("EMQX_HOST", ""))
    p.add_argument("--puerto", type=int, default=8883)
    p.add_argument("--ca", default=os.environ.get("EMQX_CA", ""), help="certificado CA del broker (PEM)")
    p.add_argument("--usuario", default=os.environ.get("EMQX_USER", ""), help="por defecto = device-id")
    p.add_argument("--token", default=os.environ.get("EMQX_TOKEN", ""))
    p.add_argument("--esperar-ack", type=float, default=8.0, help="segundos de espera del acuse lógico")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--permitir-device-no-demo", action="store_true")
    a = p.parse_args(argv)
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")

    if "DEMO" not in a.device_id.upper() and not a.permitir_device_no_demo:
        print(f"Rechazado: '{a.device_id}' no es un dispositivo de demostración. Publicar lecturas simuladas "
              "con la identidad de un nodo real contaminaría su cadena de trazabilidad.", file=sys.stderr)
        return 2

    boot_id = int(time.time())
    pasos = PERFILES[a.perfil]
    mensajes = [construir(a.device_id, boot_id, i + 1, paso, int(i * a.intervalo)) for i, paso in enumerate(pasos)]

    if a.dry_run:
        for m in mensajes:
            print(json.dumps(m, ensure_ascii=False))
        print(f"\n{len(mensajes)} mensajes válidos (perfil '{a.perfil}', boot_id {boot_id}). No se publicó nada.")
        return 0

    if not (a.host and a.token and a.ca):
        print("Faltan --host, --token y --ca (o EMQX_HOST, EMQX_TOKEN, EMQX_CA). Solo TLS: no hay modo 1883.",
              file=sys.stderr)
        return 2

    import paho.mqtt.client as mqtt  # import tardío: --dry-run no lo necesita

    acks: dict[int, str] = {}
    llego = threading.Condition()

    def al_mensaje(_c, _u, msg):
        try:
            cuerpo = json.loads(msg.payload)
        except ValueError:
            return
        if cuerpo.get("boot_id") == boot_id:
            with llego:
                acks[int(cuerpo.get("seq_no", 0))] = str(cuerpo.get("status") or cuerpo.get("estado"))
                llego.notify_all()

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{a.device_id}-{boot_id}",
                          protocol=mqtt.MQTTv311)
    cliente.username_pw_set(a.usuario or a.device_id, a.token)
    cliente.tls_set(ca_certs=a.ca)  # valida la CA y el nombre del servidor
    conectado = threading.Event()
    respuesta: dict = {}

    def al_conectar(_c, _u, _flags, codigo, _props=None):
        respuesta["codigo"] = codigo
        conectado.set()

    cliente.on_message = al_mensaje
    cliente.on_connect = al_conectar
    cliente.connect(a.host, a.puerto, keepalive=30)
    cliente.loop_start()
    if not conectado.wait(10) or respuesta["codigo"].is_failure:
        # HU-10 esc. 2: credencial inválida o revocada → el broker rechaza el CONNECT.
        print(f"Conexión rechazada por el broker: {respuesta.get('codigo', 'sin CONNACK')}", file=sys.stderr)
        cliente.loop_stop()
        return 3
    cliente.subscribe(f"farmacias/{a.device_id}/ack", qos=1)
    time.sleep(1.0)

    print(f"Perfil '{a.perfil}' · {a.device_id} · boot_id {boot_id} · {len(mensajes)} lecturas SIMULADAS\n")
    print(f"{'seq':>4}  {'temp °C':>8}  {'estado':<13}  PUBACK  acuse lógico")
    try:
        for m in mensajes:
            info = cliente.publish(f"farmacias/{a.device_id}/lecturas", json.dumps(m), qos=1)
            info.wait_for_publish(timeout=10)
            with llego:
                llego.wait_for(lambda: m["seq_no"] in acks, timeout=a.esperar_ack)
            temp = "—" if m["temp_ds18b20"] is None else f"{m['temp_ds18b20']:.2f}"
            estado = m["ds18b20_fault"] or ("FUERA 2–8" if m["temp_ds18b20"] is not None and not 2 <= m["temp_ds18b20"] <= 8 else "ok")
            print(f"{m['seq_no']:>4}  {temp:>8}  {estado:<13}  {'sí' if info.is_published() else 'NO':<6}  "
                  f"{acks.get(m['seq_no'], 'sin acuse')}")
            time.sleep(a.intervalo)
    finally:
        cliente.loop_stop()
        cliente.disconnect()
    sin_ack = sum(1 for m in mensajes if m["seq_no"] not in acks)
    print(f"\nListo. Acuses recibidos: {len(mensajes) - sin_ack}/{len(mensajes)}.")
    return 0 if sin_ack == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
