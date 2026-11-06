"""Paper (banco): una corrida por escenario, con su CSV y su resumen.

Cada corrida vive en evidencia/paper/<caso>_r<rep>_<fecha>/:
    corrida.json   caso, repetición, dispositivo, inicio y fin (UTC)
    marcas.csv     eventos que provoca el operador (apagar hotspot, reset...)
    mqtt.csv       cada mensaje que llegó al broker (paper_escucha.py): reenvíos
    lecturas.csv   una fila por registro guardado en PostgreSQL
    incidentes.csv / alertas.csv   lo que abrió el backend en la ventana
    resumen.json / resumen.md      las cifras para la tabla del paper

Uso (desde infra-local/):
    python scripts/paper_corrida.py inicio S2 1 [--nota "..."] [--horas 14]
    python scripts/paper_corrida.py marca "hotspot apagado"
    python scripts/paper_corrida.py fin
    python scripts/paper_corrida.py resumen <carpeta>   # recalcula sin tocar la base

Solo lee la base (docker exec psql) y el broker (cuenta backend_service): no
escribe nada en el sistema que se está midiendo.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
EVIDENCIA = RAIZ / "evidencia" / "paper"
ACTIVA = EVIDENCIA / ".corrida_activa"
PG = "coldcare-develop-postgres-1"
BACKEND = "coldcare-develop-backend-1"
DEVICE = "ESP32_FARMACIA_01"
INTERVALO_S = 10
ISO = "YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\""


def ahora() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def a_dt(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def psql_csv(sql: str) -> list[dict]:
    """Ejecuta un SELECT en la base local y devuelve las filas como diccionarios."""
    orden = f"COPY ({sql}) TO STDOUT WITH CSV HEADER"
    salida = subprocess.run(
        ["docker", "exec", "-i", PG, "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1'],
        input=orden, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return list(csv.DictReader(io.StringIO(salida)))


def escribir_csv(ruta: Path, filas: list[dict]) -> None:
    with ruta.open("w", newline="", encoding="utf-8") as f:
        if not filas:
            f.write("")
            return
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)


def carpeta_activa() -> Path:
    if not ACTIVA.exists():
        sys.exit("No hay corrida activa: use 'inicio'.")
    return Path(ACTIVA.read_text(encoding="utf-8").strip())


ESCUCHA = "paper-escucha"


def detener_escucha() -> None:
    """Quita el contenedor de la escucha del broker."""
    subprocess.run(["docker", "rm", "-f", ESCUCHA], capture_output=True, check=False)


def inicio(args: argparse.Namespace) -> None:
    if ACTIVA.exists():
        sys.exit(f"Ya hay una corrida activa: {ACTIVA.read_text(encoding='utf-8').strip()}. Ciérrela con 'fin'.")
    sello = datetime.now().strftime("%Y%m%d-%H%M")
    carpeta = EVIDENCIA / f"{args.caso}_r{args.rep}_{sello}"
    carpeta.mkdir(parents=True)
    datos = {"caso": args.caso, "repeticion": args.rep, "device_id": args.device, "intervalo_s": INTERVALO_S,
             "inicio_utc": ahora(), "fin_utc": None, "nota": args.nota}
    (carpeta / "corrida.json").write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    (carpeta / "marcas.csv").write_text("utc,marca\n", encoding="utf-8")
    script = (RAIZ / "scripts" / "paper_escucha.py").open("rb")
    salida = (carpeta / "mqtt.csv").open("wb")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    detener_escucha()
    # Contenedor aparte con la imagen y el entorno del backend: sigue escuchando con el backend caído (S6).
    subprocess.Popen(["docker", "compose", "-p", "coldcare-develop", "-f", str(RAIZ / "docker-compose.yml"),
                      "--profile", "backend", "run", "--rm", "--no-deps", "-T", "--name", ESCUCHA, "backend",
                      "python", "-", args.device, str(args.horas * 3600)],
                     stdin=script, stdout=salida, stderr=(carpeta / "escucha.log").open("wb"), creationflags=flags)
    ACTIVA.write_text(str(carpeta), encoding="utf-8")
    print(f"Corrida {args.caso} r{args.rep} iniciada: {carpeta.name}")


def marca(args: argparse.Namespace) -> None:
    carpeta = carpeta_activa()
    with (carpeta / "marcas.csv").open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([ahora(), args.texto])
    print(f"{ahora()}  {args.texto}")


def esperar_vaciado(device: str, tope_s: float = 1200) -> None:
    """Espera a que el nodo esté en línea y sin cola: la lectura más reciente llegó hace menos de
    2 intervalos y con latencia de lectura en línea. Cerrar antes deja fuera los diferidos."""
    import time
    limite = time.time() + tope_s
    while time.time() < limite:
        fila = psql_csv(
            "SELECT extract(epoch FROM created_at - timestamp) AS lat, extract(epoch FROM now() - created_at) AS hace "
            f"FROM thermal_readings WHERE device_id = '{device}' ORDER BY created_at DESC LIMIT 1")
        if fila and float(fila[0]["lat"]) <= 5 and float(fila[0]["hace"]) <= 2 * INTERVALO_S:
            return
        time.sleep(5)
    print("AVISO: el nodo no volvió a estar en línea sin cola dentro del tope; se cierra igual.")


def fin(args: argparse.Namespace) -> None:
    carpeta = carpeta_activa()
    datos = json.loads((carpeta / "corrida.json").read_text(encoding="utf-8"))
    if not args.sin_esperar:
        esperar_vaciado(datos["device_id"])
    datos["fin_utc"] = ahora()
    (carpeta / "corrida.json").write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    detener_escucha()
    exportar(carpeta, datos)
    ACTIVA.unlink()
    resumir(carpeta)


def exportar(carpeta: Path, datos: dict) -> None:
    dev, ini, fin_ = datos["device_id"], datos["inicio_utc"], datos["fin_utc"]
    lecturas = psql_csv(
        # Hora de captura: la estimada si existe. Una lectura sin reloj (time_quality=unsynced) se
        # guarda con la hora de LLEGADA en `timestamp` y la reconstruida en `timestamp_estimado`.
        f"SELECT boot_id, seq_no, to_char(COALESCE(timestamp_estimado, timestamp) AT TIME ZONE 'UTC', '{ISO}') AS captured_utc, "
        f"to_char(received_at AT TIME ZONE 'UTC', '{ISO}') AS received_utc, "
        f"to_char(created_at AT TIME ZONE 'UTC', '{ISO}') AS commit_utc, "
        "round(extract(epoch FROM created_at - COALESCE(timestamp_estimado, timestamp))::numeric, 3) AS latencia_s, "
        "time_quality, "
        "temperatura_interna AS temp_ds18b20, temperatura_ambiental AS temp_sht31, humedad_ambiental AS humedad, "
        "apertura_refrigerador AS puerta_abierta, payload->>'door_open_streak_s' AS puerta_racha_s, "
        "payload->>'duracion_apertura_segundos' AS puerta_abierta_s_ciclo, payload->>'sensor_fault' AS sensor_fault, "
        "payload->>'ds18b20_fault' AS ds18b20_fault, payload->>'sht31_fault' AS sht31_fault, "
        "nivel_riesgo, riesgo_efectivo "
        f"FROM thermal_readings WHERE device_id = '{dev}' AND COALESCE(timestamp_estimado, timestamp) >= '{ini}' "
        f"AND COALESCE(timestamp_estimado, timestamp) <= '{fin_}' "
        "AND NOT (time_quality = 'unsynced' AND timestamp_estimado IS NULL) "
        "ORDER BY boot_id, seq_no"
    )
    escribir_csv(carpeta / "lecturas.csv", lecturas)
    # Sin hora de captura ni estimación: su `timestamp` es la llegada, no se pueden ubicar en la
    # ventana. Se listan aparte por hora de llegada (suelen ser cola heredada de un corte anterior).
    escribir_csv(carpeta / "sin_hora.csv", psql_csv(
        f"SELECT boot_id, seq_no, uptime_ms, to_char(received_at AT TIME ZONE 'UTC', '{ISO}') AS received_utc, "
        "temperatura_interna AS temp_ds18b20 FROM thermal_readings "
        f"WHERE device_id = '{dev}' AND time_quality = 'unsynced' AND timestamp_estimado IS NULL "
        f"AND received_at >= '{ini}' AND received_at <= '{fin_}' ORDER BY boot_id, seq_no"))
    exportar_incidentes(carpeta, datos)
    escribir_csv(carpeta / "alertas.csv", psql_csv(
        f"SELECT nivel_riesgo, estado, to_char(created_at AT TIME ZONE 'UTC', '{ISO}') AS creada_utc, "
        f"to_char(iniciada_en AT TIME ZONE 'UTC', '{ISO}') AS iniciada_utc, mensaje "
        f"FROM thermal_alerts WHERE device_id = '{dev}' AND created_at >= '{ini}' AND created_at <= '{fin_}' "
        "ORDER BY created_at"))


def exportar_incidentes(carpeta: Path, datos: dict) -> None:
    """`abierto_en` es NOMINAL: el backend lo fija en último reporte + umbral. El instante real en
    que la tarea periódica (cada 30 s) detectó el silencio y abrió el incidente es `created_at`."""
    dev, ini, fin_ = datos["device_id"], datos["inicio_utc"], datos["fin_utc"]
    escribir_csv(carpeta / "incidentes.csv", psql_csv(
        f"SELECT tipo, to_char(abierto_en AT TIME ZONE 'UTC', '{ISO}') AS abierto_utc, "
        f"to_char(resuelto_en AT TIME ZONE 'UTC', '{ISO}') AS resuelto_utc, umbral_segundos, detalle, "
        f"to_char(created_at AT TIME ZONE 'UTC', '{ISO}') AS detectado_utc, "
        f"to_char(ultimo_reporte_en AT TIME ZONE 'UTC', '{ISO}') AS ultimo_reporte_utc "
        f"FROM incidentes_operativos WHERE device_id = '{dev}' AND abierto_en >= '{ini}' AND abierto_en <= '{fin_}' "
        "ORDER BY abierto_en"))


def leer(ruta: Path) -> list[dict]:
    if not ruta.exists() or ruta.stat().st_size == 0:
        return []
    with ruta.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def percentil(valores: list[float], p: float) -> float | None:
    if not valores:
        return None
    orden = sorted(valores)
    k = (len(orden) - 1) * p
    i = int(k)
    return orden[i] if i + 1 >= len(orden) else orden[i] + (orden[i + 1] - orden[i]) * (k - i)


def falla_commit(marcas: list[dict], mqtt: list[dict], lecturas: list[dict]) -> dict | None:
    """S6 y línea base: lecturas expuestas a un COMMIT que no ocurre, entre 'falla on' y 'falla off'
    (línea base: con el backend vivo, cada COMMIT falla) o entre 'falla armada' y 'backend caido'
    (S6: el backend muere con la lectura en curso).

    Expuesta = capturada en la ventana, o llegada al broker por primera vez en ella. El diseño
    evaluado (acuse de aplicación) espera el acuse y reenvía la misma lectura, y las capturadas
    mientras tanto esperan en el buffer. Un diseño que borra el registro con el PUBACK habría
    publicado cada lectura una vez al capturarla. El backend la habría recibido y no la habría
    guardado, y el nodo la habría borrado: perdida. Lo que llega con el backend DETENIDO no cuenta
    (S6 fuera de la ventana): su sesión MQTT es persistente (clean_session=False) y el broker se lo
    entrega al volver."""
    inicio = next((m["utc"] for m in marcas if m["marca"] in ("falla on", "falla armada")), None)
    fin_ = next((m["utc"] for m in marcas if m["marca"] in ("falla off", "backend caido")), None)
    if not inicio or not fin_:
        return None
    t0, t1 = a_dt(inicio), a_dt(fin_)
    primeras: dict[tuple, datetime] = {}
    llegadas = Counter()
    for m in mqtt:
        clave = (m["boot_id"], m["seq_no"])
        llegadas[clave] += 1
        t = a_dt(m["llegada_utc"])
        if clave not in primeras or t < primeras[clave]:
            primeras[clave] = t
    capturadas = {(f["boot_id"], f["seq_no"]) for f in lecturas if t0 <= a_dt(f["captured_utc"]) <= t1}
    expuestas = capturadas | {k for k, t in primeras.items() if t0 <= t <= t1}
    guardadas = {(f["boot_id"], f["seq_no"]) for f in lecturas}
    return {"desde_utc": inicio, "hasta_utc": fin_, "afectadas": len(expuestas),
            "capturadas_en_ventana": len(capturadas),
            "afectadas_guardadas": len(expuestas & guardadas),
            "perdida_estimada_solo_puback": len(expuestas),
            "llegadas_de_afectadas": sum(llegadas[k] for k in expuestas)}


def resumir(carpeta: Path) -> None:
    datos = json.loads((carpeta / "corrida.json").read_text(encoding="utf-8"))
    lecturas = leer(carpeta / "lecturas.csv")
    mqtt = [m for m in leer(carpeta / "mqtt.csv") if m["topico"].endswith("/lecturas")]
    marcas = leer(carpeta / "marcas.csv")
    incidentes, alertas = leer(carpeta / "incidentes.csv"), leer(carpeta / "alertas.csv")

    por_boot: dict[str, list[int]] = {}
    for fila in lecturas:
        por_boot.setdefault(fila["boot_id"], []).append(int(fila["seq_no"]))
    boots = {}
    for boot, seqs in por_boot.items():
        presentes = set(seqs)
        faltan = [s for s in range(min(seqs), max(seqs) + 1) if s not in presentes]
        boots[boot] = {"seq_min": min(seqs), "seq_max": max(seqs), "guardadas": len(seqs),
                       "esperadas": max(seqs) - min(seqs) + 1, "faltantes": len(faltan),
                       "faltantes_seq": faltan[:50], "duplicadas_en_base": len(seqs) - len(presentes),
                       "calidad_hora": dict(Counter(f["time_quality"] for f in lecturas if f["boot_id"] == boot))}

    # FIFO (S2): recorridas por (boot_id, seq_no), las confirmaciones no retroceden.
    fuera_de_orden = sum(1 for a, b in zip(lecturas, lecturas[1:]) if b["commit_utc"] < a["commit_utc"])
    diferidas = sum(1 for f in lecturas if f["latencia_s"] and float(f["latencia_s"]) > 2 * INTERVALO_S)

    # Cortes vistos en los datos (no dependen de las marcas). Cada tramo seguido de diferidos es un
    # corte: empieza tras la última lectura en línea previa, la reconexión es el COMMIT del primer
    # diferido del tramo y el vaciado llega hasta el último. Puede haber cortes no provocados.
    def es_dif(f: dict) -> bool:
        return bool(f["latencia_s"]) and float(f["latencia_s"]) > 2 * INTERVALO_S

    tramos: list[list[dict]] = []
    for f in lecturas:
        if es_dif(f):
            if tramos and tramos[-1][-1] is lecturas[lecturas.index(f) - 1]:
                tramos[-1].append(f)
            else:
                tramos.append([f])
    sin_datos = [a_dt(i["abierto_utc"]) for i in incidentes if i["tipo"] == "sin_datos"]
    sin_datos_det = [a_dt(i["detectado_utc"]) for i in incidentes if i["tipo"] == "sin_datos" and i.get("detectado_utc")]
    cortes = []
    for tramo in tramos:
        previas = [f for f in lecturas if not es_dif(f) and a_dt(f["captured_utc"]) < a_dt(tramo[0]["captured_utc"])]
        ultima_viva = max(previas, key=lambda f: a_dt(f["captured_utc"])) if previas else None
        reconexion = min(a_dt(f["commit_utc"]) for f in tramo)
        fin_vaciado = max(a_dt(f["commit_utc"]) for f in tramo)
        t0 = a_dt(ultima_viva["captured_utc"]) if ultima_viva else None
        abiertos = [t for t in sin_datos if t0 and t0 <= t <= reconexion]
        detectados = [t for t in sin_datos_det if t0 and t0 <= t <= reconexion]
        cortes.append({
            "ultima_en_linea_utc": ultima_viva["captured_utc"] if ultima_viva else None,
            "reconexion_utc": reconexion.isoformat().replace("+00:00", "Z"),
            "duracion_corte_s": round((reconexion - t0).total_seconds(), 1) if t0 else None,
            "diferidas": len(tramo),
            "vaciado_s": round((fin_vaciado - reconexion).total_seconds(), 1),
            "vaciado_registros_por_s": round(len(tramo) / max((fin_vaciado - reconexion).total_seconds(), 0.001), 2),
            "s_hasta_sin_datos": round((min(abiertos) - t0).total_seconds(), 1) if abiertos else None,
            "s_hasta_sin_datos_detectado": round((min(detectados) - t0).total_seconds(), 1) if detectados else None,
        })
    # Principal: el de más diferidas entre los que tienen una lectura en línea antes (un tramo al
    # inicio de la ventana es cola heredada de antes de la corrida, no un corte de esta).
    con_inicio = [c for c in cortes if c["ultima_en_linea_utc"]]
    corte = max(con_inicio, key=lambda c: c["diferidas"]) if con_inicio else None

    vistos = Counter((m["boot_id"], m["seq_no"]) for m in mqtt)
    reenvios = sum(n - 1 for n in vistos.values() if n > 1)
    guardadas = {(f["boot_id"], f["seq_no"]) for f in lecturas}
    sin_guardar = sorted(k for k in vistos if k not in guardadas)

    latencias = [float(f["latencia_s"]) for f in lecturas if f["latencia_s"] and f["time_quality"] != "unsynced"]
    en_linea = [x for x in latencias if x <= 2 * INTERVALO_S]

    def tras(marca_utc: str, eventos: list[dict], campo: str) -> float | None:
        t0 = a_dt(marca_utc)
        posteriores = [a_dt(e[campo]) for e in eventos if e.get(campo) and a_dt(e[campo]) >= t0]
        return round((min(posteriores) - t0).total_seconds(), 1) if posteriores else None

    resumen = {
        "corrida": carpeta.name, "caso": datos["caso"], "repeticion": datos["repeticion"],
        "inicio_utc": datos["inicio_utc"], "fin_utc": datos["fin_utc"],
        "duracion_min": round((a_dt(datos["fin_utc"]) - a_dt(datos["inicio_utc"])).total_seconds() / 60, 1)
        if datos["fin_utc"] else None,
        "lecturas_guardadas": len(lecturas),
        "faltantes": sum(b["faltantes"] for b in boots.values()),
        "duplicadas_en_base": sum(b["duplicadas_en_base"] for b in boots.values()),
        "diferidas": diferidas, "fuera_de_orden": fuera_de_orden,
        "sin_hora_llegadas_en_ventana": len(leer(carpeta / "sin_hora.csv")), "corte_inferido": corte, "cortes": cortes,
        "mensajes_lectura_broker": len(mqtt), "reenvios_broker": reenvios,
        "vistas_en_broker_sin_guardar": len(sin_guardar),
        "boots": boots,
        "latencia_s": {
            "n": len(latencias),
            "mediana": round(statistics.median(latencias), 3) if latencias else None,
            "p95": round(percentil(latencias, 0.95), 3) if latencias else None,
            "max": max(latencias) if latencias else None,
            "en_linea_n": len(en_linea),
            "en_linea_mediana": round(statistics.median(en_linea), 3) if en_linea else None,
            "en_linea_p95": round(percentil(en_linea, 0.95), 3) if en_linea else None,
        },
        "incidentes": [{"tipo": i["tipo"], "abierto_utc": i["abierto_utc"], "resuelto_utc": i["resuelto_utc"],
                        "detectado_utc": i.get("detectado_utc"), "ultimo_reporte_utc": i.get("ultimo_reporte_utc"),
                        "s_silencio_hasta_deteccion": round((a_dt(i["detectado_utc"]) - a_dt(i["ultimo_reporte_utc"]))
                                                            .total_seconds(), 1)
                        if i.get("detectado_utc") and i.get("ultimo_reporte_utc") else None}
                       for i in incidentes],
        "alertas": [{"nivel": a["nivel_riesgo"], "creada_utc": a["creada_utc"]} for a in alertas],
        "marcas": [{**m, "s_hasta_incidente": tras(m["utc"], incidentes, "abierto_utc"),
                    "s_hasta_incidente_detectado": tras(m["utc"], incidentes, "detectado_utc"),
                    "s_hasta_lectura": tras(m["utc"], lecturas, "commit_utc"),
                    "s_hasta_alerta": tras(m["utc"], alertas, "creada_utc")} for m in marcas],
        "falla_commit": falla_commit(marcas, mqtt, lecturas),
    }
    (carpeta / "resumen.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")

    lat = resumen["latencia_s"]
    lineas = [
        f"# {resumen['caso']} — repetición {resumen['repeticion']}", "",
        f"- Ventana: {resumen['inicio_utc']} → {resumen['fin_utc']} ({resumen['duracion_min']} min)",
        f"- Lecturas guardadas: **{resumen['lecturas_guardadas']}**; faltantes (huecos de seq_no): "
        f"**{resumen['faltantes']}**; duplicadas en la base: **{resumen['duplicadas_en_base']}**",
        f"- Diferidas (guardadas en el nodo y reenviadas, latencia > {2 * INTERVALO_S} s): {resumen['diferidas']}; "
        f"confirmadas fuera de orden: {resumen['fuera_de_orden']}; llegadas sin hora de captura (fuera del "
        f"análisis, ver sin_hora.csv): {resumen['sin_hora_llegadas_en_ventana']}",
        *([f"- Corte visto en los datos: última en línea {corte['ultima_en_linea_utc']}, reconexión "
           f"{corte['reconexion_utc']} ({corte['duracion_corte_s']} s); vaciado {corte['vaciado_s']} s "
           f"({corte['vaciado_registros_por_s']} registros/s); sin_datos a los {corte['s_hasta_sin_datos']} s "
           "de la última lectura en línea"] if corte else []),
        *([f"- Cortes en la corrida: {len(cortes)} (el principal es el de más diferidas; los demás no se "
           f"provocaron): " + "; ".join(f"{(c['ultima_en_linea_utc'] or 'cola previa')[11:19]} {c['duracion_corte_s']} s, "
                                       f"{c['diferidas']} diferidas" for c in cortes)] if len(cortes) > 1 else []),
        f"- Mensajes de lectura en el broker: {resumen['mensajes_lectura_broker']}; reenvíos (misma "
        f"boot_id+seq_no): {resumen['reenvios_broker']}; vistas en el broker sin guardar: "
        f"{resumen['vistas_en_broker_sin_guardar']}",
        f"- Latencia captura→COMMIT (s): mediana {lat['mediana']}, p95 {lat['p95']}, máx {lat['max']} (n={lat['n']}); "
        f"solo en línea (≤{2 * INTERVALO_S} s): mediana {lat['en_linea_mediana']}, p95 {lat['en_linea_p95']} "
        f"(n={lat['en_linea_n']})", "", "| boot_id | seq | guardadas | esperadas | faltantes | calidad de hora |", "|---|---|---|---|---|---|",
    ]
    lineas += [f"| {b} | {v['seq_min']}–{v['seq_max']} | {v['guardadas']} | {v['esperadas']} | {v['faltantes']} | "
               + ", ".join(f"{k} {n}" for k, n in v["calidad_hora"].items()) + " |" for b, v in boots.items()]
    if resumen["marcas"]:
        lineas += ["", "| marca (UTC) | evento | s hasta incidente | s hasta alerta |", "|---|---|---|---|"]
        lineas += [f"| {m['utc']} | {m['marca']} | {m['s_hasta_incidente']} | {m['s_hasta_alerta']} |"
                   for m in resumen["marcas"]]
    if resumen["incidentes"]:
        lineas += ["", "Incidentes: " + "; ".join(f"{i['tipo']} {i['abierto_utc']}" for i in resumen["incidentes"])]
    fc = resumen["falla_commit"]
    if fc:
        lineas += ["", f"Falla del COMMIT ({fc['desde_utc']} → {fc['hasta_utc']}): {fc['afectadas']} lecturas expuestas (capturadas o "
                   f"llegadas al broker durante la falla); {fc['afectadas_guardadas']} quedaron guardadas al final "
                   f"(pérdida del diseño: {fc['afectadas'] - fc['afectadas_guardadas']}). Pérdida estimada de un diseño "
                   f"que borra con el PUBACK: {fc['perdida_estimada_solo_puback']}. Llegadas al broker de esas lecturas: "
                   f"{fc['llegadas_de_afectadas']} (reenvíos: {fc['llegadas_de_afectadas'] - fc['afectadas']})."]
    (carpeta / "resumen.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="orden", required=True)
    a = sub.add_parser("inicio")
    a.add_argument("caso")
    a.add_argument("rep", type=int)
    a.add_argument("--nota", default="")
    a.add_argument("--device", default=DEVICE)
    a.add_argument("--horas", type=float, default=14, help="tope de la escucha del broker")
    a.set_defaults(f=inicio)
    m = sub.add_parser("marca")
    m.add_argument("texto")
    m.set_defaults(f=marca)
    f_ = sub.add_parser("fin")
    f_.add_argument("--sin-esperar", action="store_true", help="no esperar el vaciado del buffer")
    f_.set_defaults(f=fin)
    r = sub.add_parser("resumen")
    r.add_argument("carpeta", type=Path)
    r.set_defaults(f=lambda a: resumir(a.carpeta))
    x = sub.add_parser("reexportar-incidentes", help="vuelve a exportar incidentes.csv y rehace el resumen")
    x.add_argument("carpeta", type=Path)
    x.set_defaults(f=lambda a: (exportar_incidentes(a.carpeta, json.loads((a.carpeta / "corrida.json")
                                                                          .read_text(encoding="utf-8"))),
                                resumir(a.carpeta)))
    args = p.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
