"""Inyector de casos de escritorio con tabla de resultados y veredicto.

Publica telemetría v1.0 como FARM-02-TEST (base de `publicar_caso.py`), espera el
acuse y compara lo observado con lo esperado (`ok` = SI/NO por fila).

Casos (`--casos`, separados por coma; por defecto todos menos `latencia`):
  tem05_limites       TEM-05: dos lecturas normales y luego 1.9, 1.9375, 2.0, 8.0, 8.0625, 8.1, NaN y nulo.
                      Con `--db` comprueba en thermal_readings si hubo excursión (solo 1.9, 1.9375, 8.0625, 8.1);
                      las normales de arranque evitan el «primer inicio» sin historial
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
    DATABASE_URL=postgresql+asyncpg://.../coldcare_prueba \
    python scripts/inyector_casos.py --casos tem05_limites,colision --db --csv resultados.csv
    python scripts/inyector_casos.py --casos latencia --repetir 100 --pausa 8
    python scripts/inyector_casos.py --dry-run
Sale con código 1 si alguna fila no cumple lo esperado.
"""
import argparse
import asyncio
import csv
import json
import os
import ssl
import sys
import time
from pathlib import Path

import aiomqtt

RAIZ = Path(__file__).resolve().parent.parent
DEV = "FARM-02-TEST"
COMMIT = "commit_confirmado"
PAUSA_MINIMA_LATENCIA = 1.1  # s; la hora de captura tiene resolución de segundo


def base(seq: int, boot_id: int, device_id: str = DEV, **cambios) -> dict:
    cuerpo = {
        "schema_version": "1.0", "device_id": device_id, "boot_id": boot_id, "seq_no": seq,
        # None = se fija al ENVIAR (cuerpo_de). Calcularla al construir los casos daba a todas las
        # lecturas la misma hora: la alerta se reabría y 5 subidas producían 1 solo correo.
        "captured_at": None, "time_quality": "sntp_synced",
        "uptime_s": 60, "temp_ds18b20": 4.5, "ds18b20_status": "OK", "ds18b20_fault": None,
        "temp_sht31": 21.0, "humedad_sht31": 55.0, "sht31_status": "OK", "sht31_fault": None,
        "door_open": False, "door_open_s": 0, "door_open_streak_s": None, "mc38_status": "installed",
        "power_source": None, "sensor_fault": False, "firmware_version": "v1.2.0",
    }
    cuerpo.update(cambios)
    return cuerpo


def texto(cuerpo: dict) -> str:
    return json.dumps(cuerpo, separators=(",", ":"))


def ahora_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cuerpo_de(mensaje: dict) -> str:
    """Texto a publicar. La hora de captura se toma AHORA salvo que el mensaje la traiga fija."""
    if "crudo" in mensaje:
        return mensaje["crudo"]
    datos = dict(mensaje["datos"])
    if datos.get("captured_at") is None:
        datos["captured_at"] = ahora_iso()
    return texto(datos)


def construir_casos(boot_id: int, repetir: int = 1) -> dict[str, list[dict]]:
    """Cada mensaje: {datos | crudo, esperado, espera_acuse}; el texto sale de `cuerpo_de`. `esperado` es el `estado` del acuse
    (None = no debe haber acuse). `seq` crece por mensaje: nunca se reutiliza una identidad
    salvo en `colision` y `duplicado`, donde es el punto del caso."""
    contador = iter(range(1, 10_000_000))

    def msg(esperado: str | None, excursion: bool | None = None, **cambios) -> dict:
        return {"datos": base(next(contador), boot_id, **cambios), "esperado": esperado,
                "espera_acuse": esperado is not None, "excursion": excursion}

    # TEM-05: el nodo nuevo no tiene historial; dos lecturas normales antes de los límites.
    limites = [(t, t < 2.0 or t > 8.0) for t in (1.9, 1.9375, 2.0, 8.0, 8.0625, 8.1)]
    tem05 = [msg(COMMIT, False) for _ in range(2)]
    tem05 += [msg(COMMIT, excursion, temp_ds18b20=t) for t, excursion in limites]
    tem05 += [
        msg("payload_rechazado", temp_ds18b20=float("nan")),
        # Sensor en falla: valor nulo, sin excursión (no se inventa una temperatura).
        msg(COMMIT, False, temp_ds18b20=None, ds18b20_status="SENSOR_FAULT", ds18b20_fault="no_response",
            sensor_fault=True),
    ]

    casos: dict[str, list[dict]] = {
        "tem05_limites": tem05,
        "excursion": [msg(COMMIT, True, temp_ds18b20=12.0)],
        "latencia": [
            msg(COMMIT, t > 8.0, temp_ds18b20=t) for _ in range(repetir) for t in (12.0, 5.0)
        ],
        "rango_fisico": [msg("payload_rechazado", temp_ds18b20=200.0)],
        "json_malformado": [{"crudo": '{"schema_version": "1.0", "device_id": ', "esperado": None,
                             "espera_acuse": False}],
        "device_ajeno": [msg(None, device_id="OTRO-NODO")],
        "payload_grande": [{"datos": base(next(contador), boot_id, extra="x" * 6000), "esperado": None,
                            "espera_acuse": False}],
    }
    # Colisión y duplicado comparten la hora: es la MISMA lectura reenviada (o alterada).
    hora = ahora_iso()
    identidad = next(contador)
    casos["colision"] = [
        {"datos": base(identidad, boot_id, captured_at=hora, temp_ds18b20=4.5), "esperado": COMMIT,
         "espera_acuse": True},
        {"datos": base(identidad, boot_id, captured_at=hora, temp_ds18b20=6.5), "esperado": "conflicto_payload",
         "espera_acuse": True},
    ]
    repetida = next(contador)
    casos["duplicado"] = [
        {"datos": base(repetida, boot_id, captured_at=hora), "esperado": COMMIT, "espera_acuse": True},
        {"datos": base(repetida, boot_id, captured_at=hora), "esperado": "duplicado", "espera_acuse": True},
    ]
    return casos


async def excursion_en_bd(url: str, boot_id: int, seq_no: int) -> bool | None:
    """`excursion_confirmada` de la lectura (boot_id, seq_no) de FARM-02-TEST, o None si no está."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    motor = create_async_engine(url)
    try:
        async with motor.connect() as conexion:
            fila = (await conexion.execute(
                text("SELECT excursion_confirmada FROM thermal_readings "
                     "WHERE device_id = :d AND boot_id = :b AND seq_no = :s"),
                {"d": DEV, "b": boot_id, "s": seq_no},
            )).first()
    finally:
        await motor.dispose()
    return None if fila is None else bool(fila[0])


def veredicto_excursion(esperada: bool | None, observada: bool | None) -> bool:
    """Sin expectativa, o sin base a la que preguntar, no se juzga; con ambas, deben coincidir."""
    return esperada is None or observada is None or esperada == observada


def veredicto(esperado: str | None, acuse: str) -> bool:
    """`acuse` es el estado recibido, o «SIN ACUSE». Sin acuse esperado, cualquier acuse es fallo."""
    if esperado is None:
        return acuse == "SIN ACUSE"
    return acuse == esperado


async def ejecutar(
    casos: dict[str, list[dict]], espera: float, pausa: float, url_bd: str | None = None
) -> list[dict]:
    ctx = ssl.create_default_context(cafile=str(RAIZ / "certs/ca.crt"))
    token = (RAIZ / f"secrets/{DEV}.token").read_text()
    filas = []
    async with aiomqtt.Client("localhost", 8883, username=DEV, password=token, identifier=f"{DEV}-inyector",
                              tls_context=ctx) as c:
        await c.subscribe(f"farmacias/{DEV}/ack", qos=1)
        for nombre, mensajes in casos.items():
            for numero, m in enumerate(mensajes, 1):
                inicio = time.monotonic()
                await c.publish(f"farmacias/{DEV}/lecturas", cuerpo_de(m), qos=1)
                acuse = "SIN ACUSE"
                try:
                    msg = await asyncio.wait_for(anext(c.messages), espera if m["espera_acuse"] else 2.0)
                    acuse = json.loads(msg.payload).get("estado", msg.payload.decode()[:60])
                except TimeoutError:
                    pass
                observada = None
                if url_bd and m.get("excursion") is not None and acuse == COMMIT:
                    datos = m["datos"]
                    observada = await excursion_en_bd(url_bd, datos["boot_id"], datos["seq_no"])
                conforme = veredicto(m["esperado"], acuse) and veredicto_excursion(m.get("excursion"), observada)
                filas.append({
                    "caso": nombre, "mensaje": numero, "esperado": m["esperado"] or "SIN ACUSE",
                    "acuse": acuse, "excursion_esperada": _texto(m.get("excursion")),
                    "excursion_bd": _texto(observada) if url_bd else "(sin --db)",
                    "ok": "SI" if conforme else "NO",
                    "latencia_s": round(time.monotonic() - inicio, 3),
                })
                if pausa:
                    await asyncio.sleep(pausa)
    return filas


def _texto(valor: bool | None) -> str:
    return "—" if valor is None else ("SI" if valor else "NO")


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
    p.add_argument("--db", action="store_true", help="Comprueba la excursión en thermal_readings (DATABASE_URL)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    todos = construir_casos(int(time.time()), a.repetir)
    elegidos = [n for n in a.casos.split(",") if n] or [n for n in todos if n != "latencia"]
    desconocidos = [n for n in elegidos if n not in todos]
    if desconocidos:
        sys.exit(f"Casos desconocidos: {desconocidos}. Disponibles: {list(todos)}")
    casos = {n: todos[n] for n in elegidos}
    if "latencia" in casos and a.pausa < PAUSA_MINIMA_LATENCIA:
        # captured_at tiene resolución de segundo: dos subidas en el mismo segundo reabrirían la alerta.
        a.pausa = PAUSA_MINIMA_LATENCIA
    if a.dry_run:
        for n, ms in casos.items():
            for m in ms:
                print(f"[{n}] esperado={m['esperado']} -> {cuerpo_de(m)[:110]}")
        return
    filas = asyncio.run(
        ejecutar(casos, a.espera, a.pausa, os.environ.get("DATABASE_URL") if a.db else None),
        loop_factory=asyncio.SelectorEventLoop,
    )
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
