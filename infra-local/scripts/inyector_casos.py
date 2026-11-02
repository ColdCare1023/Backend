"""Inyector de casos de escritorio con tabla de resultados y veredicto.

Publica telemetría v1.0 como FARM-02-TEST (base de `publicar_caso.py`), espera el
acuse y compara lo observado con lo esperado (`ok` = SI/NO por fila).

Casos (`--casos`, separados por coma; por defecto todos menos `latencia`):
  tem05_limites       TEM-05: 1.9, 2.0, 2.1, 7.9, 8.0, 8.1 °C (acuse en todos; alerta solo 1.9 y 8.1)
  excursion           RIE-01 / RIE-03: 12.0 °C, una excursión
  latencia            LAT-02: alterna 12.0 °C y 5.0 °C `--repetir` veces (cada subida es un episodio
                      nuevo; exige ALERTA_VENTANA_NORMALIZACION_MINUTOS=0 en el backend)
  rango_fisico        200 °C: el esquema v1.0 lo rechaza (acuse `payload_rechazado`)
  json_malformado     MQT-03: texto que no es JSON (sin acuse)
  colision            MQT-06: misma identidad (boot_id, seq_no), valores distintos -> `conflicto_payload`
  device_ajeno        MQT-08: device_id del payload distinto del tópico (SIN acuse)
  payload_grande      más de 5 KB (sin acuse; LECTURA_RECHAZADA_TAMANO)
  duplicado           misma lectura dos veces: segundo acuse `duplicado`

Uso:
    python scripts/inyector_casos.py --casos tem05_limites,colision --csv resultados.csv
    python scripts/inyector_casos.py --casos latencia --repetir 100 --pausa 8
    python scripts/inyector_casos.py --dry-run
Sale con código 1 si alguna fila no cumple lo esperado.
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
COMMIT = "commit_confirmado"


def base(seq: int, boot_id: int, device_id: str = DEV, **cambios) -> dict:
    cuerpo = {
        "schema_version": "1.0", "device_id": device_id, "boot_id": boot_id, "seq_no": seq,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "time_quality": "sntp_synced",
        "uptime_s": 60, "temp_ds18b20": 4.5, "ds18b20_status": "OK", "ds18b20_fault": None,
        "temp_sht31": 21.0, "humedad_sht31": 55.0, "sht31_status": "OK", "sht31_fault": None,
        "door_open": False, "door_open_s": 0, "door_open_streak_s": None, "mc38_status": "installed",
        "power_source": None, "sensor_fault": False, "firmware_version": "v1.2.0",
    }
    cuerpo.update(cambios)
    return cuerpo


def texto(cuerpo: dict) -> str:
    return json.dumps(cuerpo, separators=(",", ":"))


def construir_casos(boot_id: int, repetir: int = 1) -> dict[str, list[dict]]:
    """Cada mensaje: {cuerpo, esperado, espera_acuse}. `esperado` es el `estado` del acuse
    (None = no debe haber acuse). `seq` crece por mensaje: nunca se reutiliza una identidad
    salvo en `colision` y `duplicado`, donde es el punto del caso."""
    contador = iter(range(1, 10_000_000))

    def msg(esperado: str | None, **cambios) -> dict:
        return {"cuerpo": texto(base(next(contador), boot_id, **cambios)), "esperado": esperado,
                "espera_acuse": esperado is not None}

    casos: dict[str, list[dict]] = {
        "tem05_limites": [msg(COMMIT, temp_ds18b20=t) for t in (1.9, 2.0, 2.1, 7.9, 8.0, 8.1)],
        "excursion": [msg(COMMIT, temp_ds18b20=12.0)],
        "latencia": [
            msg(COMMIT, temp_ds18b20=t) for _ in range(repetir) for t in (12.0, 5.0)
        ],
        "rango_fisico": [msg("payload_rechazado", temp_ds18b20=200.0)],
        "json_malformado": [{"cuerpo": '{"schema_version": "1.0", "device_id": ', "esperado": None,
                             "espera_acuse": False}],
        "device_ajeno": [msg(None, device_id="OTRO-NODO")],
        "payload_grande": [{"cuerpo": texto(base(next(contador), boot_id, extra="x" * 6000)), "esperado": None,
                            "espera_acuse": False}],
    }
    identidad = next(contador)
    casos["colision"] = [
        {"cuerpo": texto(base(identidad, boot_id, temp_ds18b20=4.5)), "esperado": COMMIT, "espera_acuse": True},
        {"cuerpo": texto(base(identidad, boot_id, temp_ds18b20=6.5)), "esperado": "conflicto_payload",
         "espera_acuse": True},
    ]
    repetida = next(contador)
    casos["duplicado"] = [
        {"cuerpo": texto(base(repetida, boot_id)), "esperado": COMMIT, "espera_acuse": True},
        {"cuerpo": texto(base(repetida, boot_id)), "esperado": "duplicado", "espera_acuse": True},
    ]
    return casos


def veredicto(esperado: str | None, acuse: str) -> bool:
    """`acuse` es el estado recibido, o «SIN ACUSE». Sin acuse esperado, cualquier acuse es fallo."""
    if esperado is None:
        return acuse == "SIN ACUSE"
    return acuse == esperado


async def ejecutar(casos: dict[str, list[dict]], espera: float, pausa: float) -> list[dict]:
    ctx = ssl.create_default_context(cafile=str(RAIZ / "certs/ca.crt"))
    token = (RAIZ / f"secrets/{DEV}.token").read_text()
    filas = []
    async with aiomqtt.Client("localhost", 8883, username=DEV, password=token, identifier=f"{DEV}-inyector",
                              tls_context=ctx) as c:
        await c.subscribe(f"farmacias/{DEV}/ack", qos=1)
        for nombre, mensajes in casos.items():
            for numero, m in enumerate(mensajes, 1):
                inicio = time.monotonic()
                await c.publish(f"farmacias/{DEV}/lecturas", m["cuerpo"], qos=1)
                acuse = "SIN ACUSE"
                try:
                    msg = await asyncio.wait_for(anext(c.messages), espera if m["espera_acuse"] else 2.0)
                    acuse = json.loads(msg.payload).get("estado", msg.payload.decode()[:60])
                except TimeoutError:
                    pass
                filas.append({
                    "caso": nombre, "mensaje": numero, "esperado": m["esperado"] or "SIN ACUSE",
                    "acuse": acuse, "ok": "SI" if veredicto(m["esperado"], acuse) else "NO",
                    "latencia_s": round(time.monotonic() - inicio, 3),
                })
                if pausa:
                    await asyncio.sleep(pausa)
    return filas


def imprimir(filas: list[dict]) -> None:
    ancho = {k: max(len(k), *(len(str(f[k])) for f in filas)) for k in filas[0]}
    print("  ".join(k.ljust(ancho[k]) for k in ancho))
    for f in filas:
        print("  ".join(str(f[k]).ljust(ancho[k]) for k in ancho))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--casos", default="")
    p.add_argument("--repetir", type=int, default=100, help="Pares 12/5 °C del caso `latencia`")
    p.add_argument("--pausa", type=float, default=0.0, help="Segundos entre mensajes (separa las excursiones)")
    p.add_argument("--espera", type=float, default=8.0)
    p.add_argument("--csv")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    todos = construir_casos(int(time.time()), a.repetir)
    elegidos = [n for n in a.casos.split(",") if n] or [n for n in todos if n != "latencia"]
    desconocidos = [n for n in elegidos if n not in todos]
    if desconocidos:
        sys.exit(f"Casos desconocidos: {desconocidos}. Disponibles: {list(todos)}")
    casos = {n: todos[n] for n in elegidos}
    if a.dry_run:
        for n, ms in casos.items():
            for m in ms:
                print(f"[{n}] esperado={m['esperado']} -> {m['cuerpo'][:110]}")
        return
    filas = asyncio.run(ejecutar(casos, a.espera, a.pausa), loop_factory=asyncio.SelectorEventLoop)
    imprimir(filas)
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0]))
            w.writeheader()
            w.writerows(filas)
        print(f"{len(filas)} filas en {a.csv}")
    fallos = [f for f in filas if f["ok"] == "NO"]
    print(f"{len(filas) - len(fallos)}/{len(filas)} conforme a lo esperado.")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
