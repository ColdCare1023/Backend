"""Inyector de casos de escritorio con tabla de resultados (MQT-03, MQT-06, MQT-08,
TEM-05, RIE-01, RIE-03, LAT-02).

Publica una telemetría v1.0 por caso (sobre la base de `publicar_caso.py`), espera
el acuse y deja una fila por caso con lo observado, para pegar en la evidencia.

Casos (`--casos`, separados por coma; por defecto todos):
  tem05_limite        8.0 °C (en rango) y 8.1 °C (fuera): el límite exacto
  excursion           12.0 °C: excursión crítica (RIE-01/RIE-03/LAT-02)
  rango_fisico        200 °C: fuera de rango físico, debe rechazarse
  json_malformado     texto que no es JSON (MQT-03)
  device_ajeno        device_id del payload distinto al del tópico (MQT-06): SIN acuse
  payload_grande      más de 5 KB (MQT-08): auditado, sin acuse
  duplicado           misma lectura dos veces: segundo acuse «duplicado»

Uso:
    python scripts/inyector_casos.py --casos excursion,tem05_limite --csv resultados.csv
    python scripts/inyector_casos.py --repetir 100 --casos excursion        # LAT-02
    python scripts/inyector_casos.py --dry-run
"""
import argparse
import asyncio
import csv
import json
import ssl
import sys
import time
from pathlib import Path

import aiomqtt

RAIZ = Path(__file__).resolve().parent.parent
DEV = "FARM-02-TEST"


def base(seq: int, device_id: str = DEV, **cambios) -> dict:
    cuerpo = {
        "schema_version": "1.0", "device_id": device_id, "boot_id": int(time.time()), "seq_no": seq,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "time_quality": "sntp_synced",
        "uptime_s": 60, "temp_ds18b20": 4.5, "ds18b20_status": "OK", "ds18b20_fault": None,
        "temp_sht31": 21.0, "humedad_sht31": 55.0, "sht31_status": "OK", "sht31_fault": None,
        "door_open": False, "door_open_s": 0, "door_open_streak_s": None, "mc38_status": "installed",
        "power_source": None, "sensor_fault": False, "firmware_version": "v1.2.0",
    }
    cuerpo.update(cambios)
    return cuerpo


def construir_casos(seq: int) -> dict[str, list[dict]]:
    """Cada caso es una lista de mensajes: {cuerpo, topico_device, espera_acuse, esperado}."""
    texto = lambda c: json.dumps(c, separators=(",", ":"))  # noqa: E731
    return {
        "tem05_limite": [
            {"cuerpo": texto(base(seq, temp_ds18b20=8.0)), "esperado": "committed"},
            {"cuerpo": texto(base(seq + 1, temp_ds18b20=8.1)), "esperado": "committed (excursión)"},
        ],
        "excursion": [{"cuerpo": texto(base(seq + 2, temp_ds18b20=12.0)), "esperado": "committed + alerta crítica"}],
        "rango_fisico": [{"cuerpo": texto(base(seq + 3, temp_ds18b20=200.0)), "esperado": "rechazada"}],
        "json_malformado": [{"cuerpo": '{"schema_version": "1.0", "device_id": ', "esperado": "sin acuse (auditar)"}],
        "device_ajeno": [{"cuerpo": texto(base(seq + 4, device_id="OTRO-NODO")), "espera_acuse": False,
                          "esperado": "SIN acuse; LECTURA_RECHAZADA_SUPLANTACION"}],
        "payload_grande": [{"cuerpo": texto(base(seq + 5, extra="x" * 6000)), "espera_acuse": False,
                            "esperado": "sin acuse; LECTURA_RECHAZADA_TAMANO"}],
        "duplicado": [
            {"cuerpo": texto(base(seq + 6)), "esperado": "committed"},
            {"cuerpo": texto(base(seq + 6)), "esperado": "duplicado"},
        ],
    }


async def ejecutar(casos: dict[str, list[dict]], espera: float, repetir: int) -> list[dict]:
    ctx = ssl.create_default_context(cafile=str(RAIZ / "certs/ca.crt"))
    token = (RAIZ / f"secrets/{DEV}.token").read_text()
    filas = []
    async with aiomqtt.Client("localhost", 8883, username=DEV, password=token, identifier=f"{DEV}-inyector",
                              tls_context=ctx) as c:
        await c.subscribe(f"farmacias/{DEV}/ack", qos=1)
        for nombre, mensajes in casos.items():
            for ronda in range(repetir):
                for numero, m in enumerate(mensajes, 1):
                    cuerpo = m["cuerpo"]
                    if repetir > 1:  # identidad nueva en cada repetición
                        try:
                            d = json.loads(cuerpo)
                            d["seq_no"] = int(d["seq_no"]) + ronda * 1000
                            cuerpo = json.dumps(d, separators=(",", ":"))
                        except (ValueError, KeyError):
                            pass
                    inicio = time.monotonic()
                    await c.publish(f"farmacias/{DEV}/lecturas", cuerpo, qos=1)
                    acuse = "(no se espera)"
                    if m.get("espera_acuse", True):
                        try:
                            msg = await asyncio.wait_for(anext(c.messages), espera)
                            acuse = json.loads(msg.payload).get("estado", msg.payload.decode()[:60])
                        except TimeoutError:
                            acuse = "SIN ACUSE"
                    filas.append({
                        "caso": nombre, "ronda": ronda + 1, "mensaje": numero, "esperado": m["esperado"],
                        "acuse": acuse, "latencia_s": round(time.monotonic() - inicio, 3),
                    })
    return filas


def imprimir(filas: list[dict]) -> None:
    ancho = {k: max(len(k), *(len(str(f[k])) for f in filas)) for k in filas[0]}
    print("  ".join(k.ljust(ancho[k]) for k in ancho))
    for f in filas:
        print("  ".join(str(f[k]).ljust(ancho[k]) for k in ancho))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--casos", default="")
    p.add_argument("--repetir", type=int, default=1)
    p.add_argument("--espera", type=float, default=8.0)
    p.add_argument("--csv")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    todos = construir_casos(int(time.time()) % 1_000_000 + 1)
    elegidos = [n for n in a.casos.split(",") if n] or list(todos)
    desconocidos = [n for n in elegidos if n not in todos]
    if desconocidos:
        sys.exit(f"Casos desconocidos: {desconocidos}. Disponibles: {list(todos)}")
    casos = {n: todos[n] for n in elegidos}
    if a.dry_run:
        for n, ms in casos.items():
            for m in ms:
                print(f"[{n}] -> {m['cuerpo'][:120]}")
        return
    filas = asyncio.run(ejecutar(casos, a.espera, a.repetir), loop_factory=asyncio.SelectorEventLoop)
    imprimir(filas)
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0]))
            w.writeheader()
            w.writerows(filas)
        print(f"{len(filas)} filas en {a.csv}")


if __name__ == "__main__":
    main()
