"""P-13: publicador MQTT de prueba para el stack local, SIN `simulated`.

Publica telemetría del contrato v1.0 con valores y horas exactos, como un nodo real, para los casos
del plan de pruebas de IA que necesitan que la lectura pase por el modelo preventivo (D01, D08, D09,
D12, D18 y D19 en local). `simulacion_excursiones/simular_excursion.py` marca `simulated=true` y el
backend no predice esas lecturas; este script nunca envía el campo `simulated`.

Seguridad. Se comprueba antes de abrir ninguna conexión:
- Broker: `localhost`, `127.0.0.1` o `::1`. Otro host exige `--permitir-host` y solo se acepta si es
  de red local (IP privada o de enlace local, nombre sin dominio, `.local`, `.lan`, `.localhost`,
  `.test`, `.internal`, `.home.arpa`). Los dominios de EMQX Cloud y de Railway se rechazan siempre.
- `device_id`: de prueba (lleva `TEST` o `PRUEBA` como segmento, p. ej. `PRUEBA_P13_01`,
  `FARM-02-TEST`) y nunca una identidad de producción (`ESP32_FARMACIA*`, `ESP32_DEMO*`,
  `ESP32_BANCO*`). Se exige también contra `localhost`, porque un puerto local puede ser un túnel.
- El dispositivo se da de alta solo en el stack local (`python scripts/crear_dispositivo.py
  PRUEBA_P13_01`). El token se lee de `secrets/<device_id>.token` y nunca se imprime.

Uso (desde infra-local/, con la pila arriba):
    python scripts/publicador_prueba_mqtt.py en_rango --lecturas 90 --temp 5.0
    python scripts/publicador_prueba_mqtt.py valores --valores 1.9,2.0,8.0,8.1
    python scripts/publicador_prueba_mqtt.py rampa --lecturas 60 --desde 5 --hasta 9
    python scripts/publicador_prueba_mqtt.py excursion --base 90 --subida 6 --pico 8.5 --evento 30 --cola 60
    python scripts/publicador_prueba_mqtt.py puerta_abierta --base 90 --evento 18 --pico 7.5 --cola 30
    python scripts/publicador_prueba_mqtt.py falla_sensor --base 90 --evento 3 --cola 30
    python scripts/publicador_prueba_mqtt.py csv --archivo escenario.csv
Opciones comunes: --device-id, --intervalo-s (10, el del banco), --inicio (horas exactas; si falta,
tiempo real), --time-quality, --sin-sht31, --sin-mc38, --registro evidencia.csv, --dry-run.

Horas: sin `--inicio`, la lectura i se captura en t0 + i·intervalo y se envía en ese instante (tiempo
real, como el nodo). Con `--inicio`, o con `captured_at` en el CSV, las horas son exactas y los
mensajes salen seguidos (`--pausa-s`); ninguna puede quedar en el futuro.

CSV (`csv --archivo`): una fila por lectura. `temp_ds18b20` es obligatoria; además `captured_at` u
`offset_s` (no ambas) y, opcionales, `ds18b20_fault`, `temp_sht31`, `humedad_sht31`, `sht31_fault`,
`door_open`, `door_open_s`, `door_open_streak_s`, `mc38_status`, `time_quality`, `power_source`.
Celda vacía = valor por defecto; `null` = nulo (`temp_ds18b20=null` es una falla del DS18B20).

Cada mensaje se valida con el contrato del backend (`src/infrastructure/mqtt/contrato_v1.py`) antes
de publicarlo. Los casos negativos (D19: 130 °C, -60 °C, `power_on_reset` con valor, SHT31 a 200 °C)
necesitan `--permitir-invalidos`; el backend debe rechazarlos con un acuse `rejected`, y se espera ese
estado con `--esperado rejected`.
Sale con 0 si todos los acuses tienen el estado esperado (o con `--sin-acuse`), 1 si alguno no, y 2 si
la guarda de seguridad o la entrada lo impiden.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
import json
import math
import queue
import re
import ssl
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

RAIZ = Path(__file__).resolve().parent.parent  # infra-local/
REPO = RAIZ.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.infrastructure.mqtt.contrato_v1 import (  # noqa: E402
    DEVICE_ID_PATRON,
    PAYLOAD_MAX_BYTES,
    UINT32,
    TelemetriaV1,
)

DEVICE_POR_DEFECTO = "PRUEBA_P13_01"
FIRMWARE_POR_DEFECTO = "v1.2.0"
HOSTS_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
# Brokers y servicios de producción: nunca, ni con --permitir-host.
SUFIJOS_NUBE = (".emqxsl.com", ".emqxsl.cn", ".emqx.cloud", ".emqxcloud.com", ".emqx.io", ".railway.app", ".rlwy.net")
SUFIJOS_RED_LOCAL = (".local", ".lan", ".localhost", ".test", ".internal", ".home.arpa")
PREFIJOS_PRODUCCION = ("ESP32_FARMACIA", "ESP32_DEMO", "ESP32_BANCO")
PATRON_PRUEBA = re.compile(r"(?:^|[_-])(?:TEST|PRUEBA)(?:[_-]|\d|$)", re.IGNORECASE)
DERIVA_FUTURO_S = 60  # holgura del reloj local frente al del backend
ESTADOS_ACUSE = ("committed", "duplicate", "rejected")
COLUMNAS_CSV = frozenset({
    "offset_s", "captured_at", "temp_ds18b20", "ds18b20_fault", "temp_sht31", "humedad_sht31",
    "sht31_fault", "door_open", "door_open_s", "door_open_streak_s", "mc38_status", "time_quality",
    "power_source",
})


class ErrorEntrada(ValueError):
    """Guarda de seguridad o entrada inválida: el script no publica nada."""


# ── Guarda de seguridad ───────────────────────────────────────────────────


def validar_device_de_prueba(device_id: str) -> None:
    if not re.fullmatch(DEVICE_ID_PATRON, device_id):
        raise ErrorEntrada(f"device_id inválido: {device_id!r}")
    mayus = device_id.upper()
    if mayus.startswith(PREFIJOS_PRODUCCION) or "FARMACIA" in mayus:
        raise ErrorEntrada(
            f"{device_id} es una identidad de producción o del banco físico; use un dispositivo de "
            "prueba dado de alta solo en el stack local (p. ej. PRUEBA_P13_01)."
        )
    if not PATRON_PRUEBA.search(device_id):
        raise ErrorEntrada(
            f"{device_id} no es un device_id de prueba: debe llevar TEST o PRUEBA como segmento "
            "(PRUEBA_P13_01, FARM-02-TEST)."
        )


def _normalizar_host(host: str) -> str:
    h = host.strip().lower().rstrip(".")
    return h[1:-1] if h.startswith("[") and h.endswith("]") else h


def _es_host_de_red_local(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith(SUFIJOS_RED_LOCAL)
    return ip.is_loopback or ip.is_private or ip.is_link_local


def validar_destino(host: str, device_id: str, permitir_host: bool) -> None:
    """Rechaza (ErrorEntrada) cualquier destino que no sea el stack local y cualquier
    identidad que no sea de prueba. Se llama antes de leer el token o conectar."""
    validar_device_de_prueba(device_id)
    h = _normalizar_host(host)
    if not h:
        raise ErrorEntrada("host del broker vacío")
    if h in HOSTS_LOOPBACK:
        return
    if any(h == sufijo[1:] or h.endswith(sufijo) for sufijo in SUFIJOS_NUBE):
        raise ErrorEntrada(f"{host} es un broker o servicio en la nube; este publicador es solo para el stack local.")
    if not permitir_host:
        raise ErrorEntrada(
            f"{host} no es localhost/127.0.0.1. Para el broker local en la LAN (p. ej. el del banco) "
            "añada --permitir-host."
        )
    if not _es_host_de_red_local(h):
        raise ErrorEntrada(f"{host} no es una dirección de red local; --permitir-host no la cubre.")


# ── Escenarios ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Lectura:
    """Una lectura planificada. `offset_s` ordena y marca el ritmo; `captured_at` solo se
    fija si la hora es exacta (CSV con `captured_at`)."""

    offset_s: float
    temp_ds18b20: float | None
    ds18b20_fault: str | None = None
    temp_sht31: float | None = 21.0
    humedad_sht31: float | None = 55.0
    sht31_fault: str | None = None
    door_open: bool | None = False
    door_open_s: int | None = 0
    door_open_streak_s: int | None = None
    mc38_status: str = "installed"
    time_quality: str = "sntp_synced"
    power_source: str | None = None
    captured_at: datetime | None = None
    hora_exacta: bool = False


def _redondear(valor: float) -> float:
    return round(valor, 4)


def _lineal(desde: float, hasta: float, n: int) -> list[float]:
    """n valores de `desde` a `hasta` con ambos extremos (n=1 → [hasta])."""
    if n <= 0:
        return []
    if n == 1:
        return [_redondear(hasta)]
    return [_redondear(desde + (hasta - desde) * i / (n - 1)) for i in range(n)]


@dataclass(frozen=True)
class Tramos:
    """base (temp) → subida (temp→pico) → evento (pico) → bajada (pico→temp) → cola (temp)."""

    base: int = 3
    subida: int = 0
    evento: int = 3
    bajada: int = 0
    cola: int = 3
    temp: float = 5.0
    pico: float | None = None

    def validar(self) -> None:
        if min(self.base, self.subida, self.evento, self.bajada, self.cola) < 0:
            raise ErrorEntrada("los tramos no pueden tener un número negativo de lecturas")
        if self.base + self.subida + self.evento + self.bajada + self.cola == 0:
            raise ErrorEntrada("el escenario no tiene lecturas")


def _temperaturas(t: Tramos, pico: float) -> list[tuple[str, float]]:
    subida = _lineal(t.temp, pico, t.subida + 1)[1:] if t.subida else []
    bajada = _lineal(pico, t.temp, t.bajada + 1)[1:] if t.bajada else []
    return (
        [("base", _redondear(t.temp))] * t.base
        + [("subida", v) for v in subida]
        + [("evento", _redondear(pico))] * t.evento
        + [("bajada", v) for v in bajada]
        + [("cola", _redondear(t.temp))] * t.cola
    )


def escenario_valores(valores: Iterable[float | None]) -> list[dict]:
    return [{"temp_ds18b20": None if v is None else _redondear(v)} for v in valores]


def escenario_en_rango(lecturas: int, temp: float) -> list[dict]:
    if lecturas <= 0:
        raise ErrorEntrada("--lecturas debe ser mayor que 0")
    return escenario_valores([temp] * lecturas)


def escenario_rampa(lecturas: int, desde: float, hasta: float) -> list[dict]:
    if lecturas < 2:
        raise ErrorEntrada("una rampa necesita al menos 2 lecturas")
    return escenario_valores(_lineal(desde, hasta, lecturas))


def escenario_excursion(t: Tramos) -> list[dict]:
    t.validar()
    return [{"temp_ds18b20": v} for _, v in _temperaturas(t, 8.5 if t.pico is None else t.pico)]


def escenario_puerta_abierta(t: Tramos, intervalo_s: float) -> list[dict]:
    """La puerta está abierta durante subida + evento: `door_open_s` son los segundos abiertos del
    ciclo y `door_open_streak_s` los que lleva abierta sin cerrarse (contrato v1)."""
    t.validar()
    filas, abierta = [], 0
    paso = min(65535, round(intervalo_s))
    for tramo, temp in _temperaturas(t, t.temp if t.pico is None else t.pico):
        fila: dict = {"temp_ds18b20": temp}
        if tramo in ("subida", "evento"):
            abierta += 1
            fila.update(door_open=True, door_open_s=paso, door_open_streak_s=min(65535, round(abierta * intervalo_s)))
        filas.append(fila)
    return filas


def escenario_falla_sensor(t: Tramos, motivo: str) -> list[dict]:
    """DS18B20 en falla (valor null, SENSOR_FAULT) durante el evento; el resto en `temp`."""
    t.validar()
    filas = []
    for tramo, temp in _temperaturas(t, t.temp if t.pico is None else t.pico):
        filas.append({"temp_ds18b20": None, "ds18b20_fault": motivo} if tramo == "evento" else {"temp_ds18b20": temp})
    return filas


def _a_hora(texto: str) -> datetime:
    valor = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    if valor.tzinfo is None:
        raise ErrorEntrada(f"hora sin zona: {texto!r}; use UTC explícita (…Z o +00:00)")
    return valor.astimezone(timezone.utc)


def _celda(fila: dict, columna: str, convertir: Callable[[str], object], defecto: object) -> object:
    texto = (fila.get(columna) or "").strip()
    if texto == "":
        return defecto
    if texto.lower() == "null":
        return None
    try:
        return convertir(texto)
    except ValueError as error:
        raise ErrorEntrada(f"{columna}={texto!r}: {error}") from None


def _booleano(texto: str) -> bool:
    valor = texto.strip().lower()
    if valor in ("true", "1"):
        return True
    if valor in ("false", "0"):
        return False
    raise ValueError("se esperaba true/false")


def _entero(texto: str) -> int:
    return int(texto)


def leer_csv(ruta: Path, intervalo_s: float) -> list[Lectura]:
    with ruta.open(encoding="utf-8", newline="") as archivo:
        lector = csv.DictReader(archivo)
        columnas = {c.strip() for c in (lector.fieldnames or [])}
        if "simulated" in columnas:
            raise ErrorEntrada("la columna `simulated` no se admite: este publicador nunca la envía (P-13).")
        desconocidas = sorted(columnas - COLUMNAS_CSV)
        if desconocidas:
            raise ErrorEntrada(f"columnas desconocidas en {ruta.name}: {desconocidas}")
        if "temp_ds18b20" not in columnas:
            raise ErrorEntrada(f"{ruta.name}: falta la columna temp_ds18b20")
        if {"offset_s", "captured_at"} <= columnas:
            raise ErrorEntrada(f"{ruta.name}: use captured_at u offset_s, no ambas")
        filas = [{(k or "").strip(): v for k, v in fila.items()} for fila in lector]
    if not filas:
        raise ErrorEntrada(f"{ruta.name} no tiene filas")
    lecturas: list[Lectura] = []
    anterior: float | None = None
    base_absoluta: datetime | None = None
    for numero, fila in enumerate(filas, start=2):
        try:
            temp = fila.get("temp_ds18b20", "").strip()
            if temp == "":
                raise ErrorEntrada("temp_ds18b20 vacía (use null para una falla del DS18B20)")
            captured_at = None
            if "captured_at" in columnas:
                captured_at = _celda(fila, "captured_at", _a_hora, None)
                if captured_at is not None and base_absoluta is None:
                    base_absoluta = captured_at
            tq = _celda(fila, "time_quality", str, "sntp_synced")
            if "captured_at" in columnas and captured_at is None and tq != "unsynced":
                raise ErrorEntrada("captured_at vacío o null solo con time_quality=unsynced")
            if captured_at is not None:
                offset = (captured_at - base_absoluta).total_seconds()
            elif "offset_s" in columnas and (fila.get("offset_s") or "").strip():
                offset = float(fila["offset_s"])
            else:
                offset = 0.0 if anterior is None else anterior + intervalo_s
            if anterior is not None and offset < anterior:
                raise ErrorEntrada("las filas deben ir en orden de captura")
            anterior = offset
            mc38 = _celda(fila, "mc38_status", str, "installed")
            lecturas.append(Lectura(
                offset_s=offset,
                temp_ds18b20=_celda(fila, "temp_ds18b20", float, None),
                ds18b20_fault=_celda(fila, "ds18b20_fault", str, None),
                temp_sht31=_celda(fila, "temp_sht31", float, 21.0),
                humedad_sht31=_celda(fila, "humedad_sht31", float, 55.0),
                sht31_fault=_celda(fila, "sht31_fault", str, None),
                door_open=_celda(fila, "door_open", _booleano, False if mc38 == "installed" else None),
                door_open_s=_celda(fila, "door_open_s", _entero, 0 if mc38 == "installed" else None),
                door_open_streak_s=_celda(fila, "door_open_streak_s", _entero, None),
                mc38_status=mc38,
                time_quality=tq,
                power_source=_celda(fila, "power_source", str, None),
                captured_at=captured_at,
                hora_exacta="captured_at" in columnas,
            ))
        except (ErrorEntrada, ValueError) as error:
            raise ErrorEntrada(f"{ruta.name}, fila {numero}: {error}") from None
    return lecturas


def lecturas_de_escenario(
    filas: list[dict], intervalo_s: float, *, time_quality: str, sin_sht31: bool, sin_mc38: bool
) -> list[Lectura]:
    if intervalo_s <= 0:
        raise ErrorEntrada("--intervalo-s debe ser mayor que 0")
    lecturas = []
    for i, fila in enumerate(filas):
        lectura = Lectura(offset_s=i * intervalo_s, time_quality=time_quality, **fila)
        if sin_sht31:
            lectura = replace(lectura, temp_sht31=None, humedad_sht31=None, sht31_fault="no_response")
        if sin_mc38:
            lectura = replace(lectura, mc38_status="not_installed", door_open=None, door_open_s=None,
                              door_open_streak_s=None)
        lecturas.append(lectura)
    return lecturas


# ── Mensajes ──────────────────────────────────────────────────────────────


def _iso(instante: datetime) -> str:
    instante = instante.astimezone(timezone.utc)
    if instante.microsecond:
        return instante.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return instante.strftime("%Y-%m-%dT%H:%M:%SZ")


def construir_payload(
    lectura: Lectura, *, device_id: str, boot_id: int, seq_no: int, uptime_s: int, captured_at: datetime | None,
    firmware_version: str = FIRMWARE_POR_DEFECTO,
) -> dict:
    """Telemetría v1.0 coherente con las reglas cruzadas del contrato. No incluye `simulated`."""
    ds_fault = lectura.ds18b20_fault
    if lectura.temp_ds18b20 is None and ds_fault is None:
        ds_fault = "no_response"
    ds_ok = lectura.temp_ds18b20 is not None and ds_fault is None
    sht_fault = lectura.sht31_fault
    if (lectura.temp_sht31 is None and lectura.humedad_sht31 is None) and sht_fault is None:
        sht_fault = "no_response"
    sht_ok = lectura.temp_sht31 is not None and lectura.humedad_sht31 is not None and sht_fault is None
    sin_mc38 = lectura.mc38_status == "not_installed"
    sin_hora = lectura.time_quality == "unsynced"
    return {
        "schema_version": "1.0",
        "device_id": device_id,
        "boot_id": boot_id,
        "seq_no": seq_no,
        "captured_at": None if sin_hora or captured_at is None else _iso(captured_at),
        "time_quality": lectura.time_quality,
        "uptime_s": uptime_s,
        "temp_ds18b20": lectura.temp_ds18b20,
        "ds18b20_status": "OK" if ds_ok else "SENSOR_FAULT",
        "ds18b20_fault": ds_fault,
        "temp_sht31": lectura.temp_sht31,
        "humedad_sht31": lectura.humedad_sht31,
        "sht31_status": "OK" if sht_ok else "SENSOR_FAULT",
        "sht31_fault": sht_fault,
        "door_open": None if sin_mc38 else lectura.door_open,
        "door_open_s": None if sin_mc38 else lectura.door_open_s,
        "door_open_streak_s": None if sin_mc38 else lectura.door_open_streak_s,
        "mc38_status": lectura.mc38_status,
        "power_source": lectura.power_source,
        "sensor_fault": not (ds_ok and sht_ok),
        "firmware_version": firmware_version,
    }


def serializar(payload: dict) -> bytes:
    # Compacto, como el firmware. NaN solo llega aquí con --permitir-invalidos.
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def errores_de_contrato(cuerpo: bytes) -> str | None:
    if len(cuerpo) > PAYLOAD_MAX_BYTES:
        return f"{len(cuerpo)} B excede {PAYLOAD_MAX_BYTES} B"
    try:
        TelemetriaV1.model_validate(json.loads(cuerpo))
    except ValidationError as error:
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'mensaje'}: {e['msg']}" for e in error.errors())
    except ValueError as error:
        return str(error) or type(error).__name__
    return None


@dataclass
class Mensaje:
    seq_no: int
    boot_id: int
    envio: datetime  # instante planificado de captura/envío
    payload: dict
    cuerpo: bytes
    error_contrato: str | None = None

    @property
    def huella(self) -> str:
        return hashlib.sha256(self.cuerpo).hexdigest()


def planificar(
    lecturas: list[Lectura], *, device_id: str, boot_id: int, seq_inicial: int, uptime_inicial: int,
    t0: datetime, firmware_version: str = FIRMWARE_POR_DEFECTO, permitir_invalidos: bool = False,
) -> list[Mensaje]:
    """Asigna identidad (boot_id, seq_no), hora y cuerpo a cada lectura y valida el contrato."""
    if not 0 <= boot_id <= UINT32:
        raise ErrorEntrada("--boot-id fuera de rango (0..4294967295)")
    if seq_inicial < 1 or seq_inicial + len(lecturas) - 1 > UINT32:
        raise ErrorEntrada("--seq-inicial fuera de rango")
    mensajes = []
    for i, lectura in enumerate(lecturas):
        if lectura.hora_exacta and lectura.captured_at is not None:
            hora = lectura.captured_at
        else:
            hora = t0 + timedelta(seconds=lectura.offset_s)
        payload = construir_payload(
            lectura, device_id=device_id, boot_id=boot_id, seq_no=seq_inicial + i,
            uptime_s=min(UINT32, uptime_inicial + int(lectura.offset_s)), captured_at=hora,
            firmware_version=firmware_version,
        )
        cuerpo = serializar(payload)
        error = errores_de_contrato(cuerpo)
        if error and not permitir_invalidos:
            raise ErrorEntrada(
                f"la lectura {i + 1} (seq_no {seq_inicial + i}) no cumple el contrato v1.0: {error}. "
                "Para un caso negativo use --permitir-invalidos."
            )
        mensajes.append(Mensaje(seq_inicial + i, boot_id, hora, payload, cuerpo, error))
    return mensajes


def validar_horas(mensajes: list[Mensaje], ahora: datetime) -> None:
    limite = ahora + timedelta(seconds=DERIVA_FUTURO_S)
    futuras = [m.seq_no for m in mensajes if m.payload["captured_at"] is not None and m.envio > limite]
    if futuras:
        raise ErrorEntrada(
            f"{len(futuras)} lectura(s) con hora futura (primera seq_no {futuras[0]}): el backend las "
            "rechazaría. Ajuste --inicio o use tiempo real (sin --inicio)."
        )


# ── Publicación ───────────────────────────────────────────────────────────


class Transporte(Protocol):
    def publicar(self, topico: str, cuerpo: bytes) -> None: ...

    def esperar_acuse(self, boot_id: int, seq_no: int, timeout_s: float) -> dict | None: ...

    def cerrar(self) -> None: ...


class TransportePaho:
    """La misma sesión que el nodo: client_id y usuario = device_id, contraseña = token, TLS
    validando CA y host, QoS 1 y suscripción a `farmacias/<id>/ack`."""

    def __init__(self, host: str, puerto: int, device_id: str, token: str, ca: Path, timeout_s: float = 10) -> None:
        import paho.mqtt.client as mqtt

        self._acuses: queue.Queue[dict] = queue.Queue()
        self._pendientes: dict[tuple[int, int], dict] = {}
        conexion: queue.Queue[object] = queue.Queue()
        self._cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=device_id, protocol=mqtt.MQTTv311, clean_session=True
        )
        self._cliente.username_pw_set(device_id, token)
        contexto = ssl.create_default_context(cafile=str(ca))
        self._cliente.tls_set_context(contexto)
        self._cliente.on_connect = lambda _c, _u, _f, codigo, _p=None: conexion.put(codigo)
        self._cliente.on_message = self._al_recibir
        self._cliente.connect(host, puerto, keepalive=30)
        self._cliente.loop_start()
        try:
            codigo = conexion.get(timeout=timeout_s)
        except queue.Empty:
            self.cerrar()
            raise ConnectionError(f"sin CONNACK de {host}:{puerto} en {timeout_s} s") from None
        if getattr(codigo, "is_failure", False) or (isinstance(codigo, int) and codigo != 0):
            self.cerrar()
            raise ConnectionError(f"el broker rechazó la conexión: {codigo}")
        resultado, mid = self._cliente.subscribe(f"farmacias/{device_id}/ack", qos=1)
        if resultado != 0:
            self.cerrar()
            raise ConnectionError(f"no se pudo suscribir al acuse (código {resultado}, mid {mid})")

    def _al_recibir(self, _cliente, _datos, mensaje) -> None:
        try:
            self._acuses.put(json.loads(mensaje.payload))
        except ValueError:
            pass

    def publicar(self, topico: str, cuerpo: bytes) -> None:
        self._cliente.publish(topico, cuerpo, qos=1).wait_for_publish(timeout=10)

    def esperar_acuse(self, boot_id: int, seq_no: int, timeout_s: float) -> dict | None:
        limite = time.monotonic() + timeout_s
        while (clave := (boot_id, seq_no)) not in self._pendientes:
            restante = limite - time.monotonic()
            if restante <= 0:
                return None
            try:
                acuse = self._acuses.get(timeout=restante)
            except queue.Empty:
                return None
            self._pendientes[(acuse.get("boot_id"), acuse.get("seq_no"))] = acuse
        return self._pendientes.pop(clave)

    def cerrar(self) -> None:
        self._cliente.loop_stop()
        self._cliente.disconnect()


@dataclass
class Resultado:
    mensaje: Mensaje
    estado: str  # committed / duplicate / rejected / sin_acuse / no_esperado
    huella_ok: bool | None = None
    detalle: dict = field(default_factory=dict)


def publicar_todos(
    mensajes: list[Mensaje], transporte: Transporte, device_id: str, *, tiempo_real: bool, pausa_s: float,
    esperar_acuse: bool, timeout_s: float, reloj: Callable[[], datetime] | None = None,
    dormir: Callable[[float], None] = time.sleep, al_publicar: Callable[[Resultado], None] | None = None,
) -> list[Resultado]:
    reloj = reloj or (lambda: datetime.now(timezone.utc))
    topico = f"farmacias/{device_id}/lecturas"
    resultados = []
    for i, mensaje in enumerate(mensajes):
        if tiempo_real:
            espera = (mensaje.envio - reloj()).total_seconds()
            if espera > 0:
                dormir(espera)
        elif i and pausa_s > 0:
            dormir(pausa_s)
        transporte.publicar(topico, mensaje.cuerpo)
        if not esperar_acuse:
            resultado = Resultado(mensaje, "no_esperado")
        else:
            acuse = transporte.esperar_acuse(mensaje.boot_id, mensaje.seq_no, timeout_s)
            if acuse is None:
                resultado = Resultado(mensaje, "sin_acuse")
            else:
                estado = acuse.get("status") if acuse.get("status") in ESTADOS_ACUSE else "desconocido"
                resultado = Resultado(mensaje, estado, acuse.get("content_sha256") == mensaje.huella, acuse)
        resultados.append(resultado)
        if al_publicar:
            al_publicar(resultado)
    return resultados


def cumple(resultados: list[Resultado], esperados: set[str]) -> bool:
    return all(
        r.estado == "no_esperado" or (r.estado in esperados and r.huella_ok is not False) for r in resultados
    )


def escribir_registro(ruta: Path, resultados: list[Resultado]) -> None:
    with ruta.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow([
            "boot_id", "seq_no", "captured_at", "time_quality", "temp_ds18b20", "ds18b20_status", "door_open",
            "error_contrato", "acuse", "huella_ok", "content_sha256", "cuerpo",
        ])
        for r in resultados:
            p = r.mensaje.payload
            escritor.writerow([
                r.mensaje.boot_id, r.mensaje.seq_no, p["captured_at"] or "", p["time_quality"],
                "" if p["temp_ds18b20"] is None else p["temp_ds18b20"], p["ds18b20_status"],
                "" if p["door_open"] is None else str(p["door_open"]).lower(), r.mensaje.error_contrato or "",
                r.estado, "" if r.huella_ok is None else str(r.huella_ok).lower(), r.mensaje.huella,
                r.mensaje.cuerpo.decode(),
            ])


# ── CLI ───────────────────────────────────────────────────────────────────


def _valores(texto: str) -> list[float | None]:
    salida: list[float | None] = []
    for parte in texto.split(","):
        parte = parte.strip()
        if not parte:
            continue
        salida.append(None if parte.lower() == "null" else float(parte))
    if not salida:
        raise argparse.ArgumentTypeError("lista vacía")
    return salida


def _parser() -> argparse.ArgumentParser:
    comun = argparse.ArgumentParser(add_help=False)
    comun.add_argument("--device-id", default=DEVICE_POR_DEFECTO)
    comun.add_argument("--host", default="localhost")
    comun.add_argument("--puerto", type=int, default=8883)
    comun.add_argument("--permitir-host", action="store_true",
                       help="broker local en la LAN (IP privada o nombre local); nunca la nube")
    comun.add_argument("--ca", type=Path, default=RAIZ / "certs" / "ca.crt")
    comun.add_argument("--token-archivo", type=Path, default=None,
                       help="por defecto secrets/<device_id>.token (no se imprime)")
    comun.add_argument("--intervalo-s", type=float, default=10.0)
    comun.add_argument("--inicio", default=None, help="hora exacta de la primera lectura (ISO 8601 UTC)")
    comun.add_argument("--pausa-s", type=float, default=0.2, help="entre mensajes con horas exactas")
    comun.add_argument("--boot-id", type=int, default=None, help="por defecto, un arranque nuevo por ejecución")
    comun.add_argument("--seq-inicial", type=int, default=1)
    comun.add_argument("--uptime-inicial", type=int, default=600)
    comun.add_argument("--firmware", default=FIRMWARE_POR_DEFECTO)
    comun.add_argument("--time-quality", choices=("sntp_synced", "sntp_backfilled", "unsynced"), default="sntp_synced")
    comun.add_argument("--sin-sht31", action="store_true", help="SHT31 en falla (no_response)")
    comun.add_argument("--sin-mc38", action="store_true", help="nodo sin MC-38 (puerta null)")
    comun.add_argument("--permitir-invalidos", action="store_true", help="publica mensajes fuera del contrato (D19)")
    comun.add_argument("--sin-acuse", action="store_true")
    comun.add_argument("--acuse-timeout-s", type=float, default=8.0)
    comun.add_argument("--esperado", default="committed", help="estados de acuse aceptados, separados por coma")
    comun.add_argument("--registro", type=Path, default=None, help="CSV con cada mensaje y su acuse")
    comun.add_argument("--dry-run", action="store_true", help="imprime los mensajes; no se conecta")

    tramos = argparse.ArgumentParser(add_help=False)
    tramos.add_argument("--base", type=int, default=3)
    tramos.add_argument("--subida", type=int, default=0)
    tramos.add_argument("--evento", type=int, default=3)
    tramos.add_argument("--bajada", type=int, default=0)
    tramos.add_argument("--cola", type=int, default=3)
    tramos.add_argument("--temp", type=float, default=5.0)
    tramos.add_argument("--pico", type=float, default=None)

    parser = argparse.ArgumentParser(prog="publicador_prueba_mqtt", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="escenario", required=True)
    p = sub.add_parser("en_rango", parents=[comun])
    p.add_argument("--lecturas", type=int, default=10)
    p.add_argument("--temp", type=float, default=5.0)
    p = sub.add_parser("rampa", parents=[comun])
    p.add_argument("--lecturas", type=int, default=10)
    p.add_argument("--desde", type=float, required=True)
    p.add_argument("--hasta", type=float, required=True)
    p = sub.add_parser("valores", parents=[comun])
    p.add_argument("--valores", type=_valores, required=True, help="p. ej. 1.9,2.0,8.0,8.1,null")
    sub.add_parser("excursion", parents=[comun, tramos])
    sub.add_parser("puerta_abierta", parents=[comun, tramos])
    p = sub.add_parser("falla_sensor", parents=[comun, tramos])
    p.add_argument("--motivo", default="no_response",
                   choices=("no_response", "crc_error", "timeout", "implausible", "power_on_reset"))
    p = sub.add_parser("csv", parents=[comun])
    p.add_argument("--archivo", type=Path, required=True)
    return parser


def construir_lecturas(args: argparse.Namespace) -> list[Lectura]:
    if args.escenario == "csv":
        return leer_csv(args.archivo, args.intervalo_s)
    if args.escenario == "en_rango":
        filas = escenario_en_rango(args.lecturas, args.temp)
    elif args.escenario == "rampa":
        filas = escenario_rampa(args.lecturas, args.desde, args.hasta)
    elif args.escenario == "valores":
        filas = escenario_valores(args.valores)
    else:
        t = Tramos(args.base, args.subida, args.evento, args.bajada, args.cola, args.temp, args.pico)
        if args.escenario == "excursion":
            filas = escenario_excursion(t)
        elif args.escenario == "puerta_abierta":
            filas = escenario_puerta_abierta(t, args.intervalo_s)
        else:
            filas = escenario_falla_sensor(t, args.motivo)
    return lecturas_de_escenario(
        filas, args.intervalo_s, time_quality=args.time_quality, sin_sht31=args.sin_sht31, sin_mc38=args.sin_mc38
    )


def _imprimir(resultado: Resultado) -> None:
    p = resultado.mensaje.payload
    temp = "null" if p["temp_ds18b20"] is None else p["temp_ds18b20"]
    if isinstance(temp, float) and math.isnan(temp):
        temp = "NaN"
    huella = "" if resultado.huella_ok is None else (" huella=ok" if resultado.huella_ok else " huella=NO COINCIDE")
    print(f"seq {resultado.mensaje.seq_no:>6}  {p['captured_at'] or '(sin hora)':<24} {temp!s:>8} °C  "
          f"puerta={p['door_open']!s:<5} -> {resultado.estado}{huella}")


def main(argv: list[str] | None = None, transporte_factory: Callable[..., Transporte] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        # La guarda va primero: con un destino o una identidad no permitidos no se lee el token
        # ni se abre ninguna conexión, tampoco en --dry-run.
        validar_destino(args.host, args.device_id, args.permitir_host)
        if args.intervalo_s <= 0:
            raise ErrorEntrada("--intervalo-s debe ser mayor que 0")
        esperados = {e.strip() for e in args.esperado.split(",") if e.strip()}
        if not esperados <= set(ESTADOS_ACUSE):
            raise ErrorEntrada(f"--esperado admite {', '.join(ESTADOS_ACUSE)}")
        lecturas = construir_lecturas(args)
        ahora = datetime.now(timezone.utc).replace(microsecond=0)
        exactas = args.inicio is not None or any(lectura.hora_exacta for lectura in lecturas)
        t0 = _a_hora(args.inicio) if args.inicio else ahora
        boot_id = args.boot_id if args.boot_id is not None else int(time.time()) % UINT32
        mensajes = planificar(
            lecturas, device_id=args.device_id, boot_id=boot_id, seq_inicial=args.seq_inicial,
            uptime_inicial=args.uptime_inicial, t0=t0, firmware_version=args.firmware,
            permitir_invalidos=args.permitir_invalidos,
        )
        if exactas:
            validar_horas(mensajes, ahora)
    except (ErrorEntrada, OSError) as error:
        print(f"No se publica nada: {error}", file=sys.stderr)
        return 2

    if args.dry_run:
        for mensaje in mensajes:
            print(mensaje.cuerpo.decode())
        return 0

    token_archivo = args.token_archivo or RAIZ / "secrets" / f"{args.device_id}.token"
    if not token_archivo.is_file():
        print(f"No se publica nada: falta {token_archivo} (dé de alta el dispositivo en el stack local con "
              f"`python scripts/crear_dispositivo.py {args.device_id}`).", file=sys.stderr)
        return 2
    token = token_archivo.read_text(encoding="utf-8").strip()
    fabrica = transporte_factory or TransportePaho
    transporte = fabrica(args.host, args.puerto, args.device_id, token, args.ca)
    try:
        print(f"{len(mensajes)} lecturas de {args.device_id} (boot_id {boot_id}) a {args.host}:{args.puerto}, "
              f"{'horas exactas' if exactas else 'tiempo real'}; sin `simulated`.")
        resultados = publicar_todos(
            mensajes, transporte, args.device_id, tiempo_real=not exactas, pausa_s=args.pausa_s,
            esperar_acuse=not args.sin_acuse, timeout_s=args.acuse_timeout_s, al_publicar=_imprimir,
        )
    finally:
        transporte.cerrar()
    if args.registro:
        escribir_registro(args.registro, resultados)
    ok = cumple(resultados, esperados)
    print("Todos los acuses con el estado esperado." if ok else "Hay acuses ausentes o con un estado no esperado.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
