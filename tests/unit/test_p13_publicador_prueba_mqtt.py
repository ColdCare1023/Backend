"""P-13: publicador MQTT de prueba para el stack local (sin `simulated`).

Cubre la construcción del mensaje contra el contrato v1.0 del backend, los escenarios con valores y
horas exactos, el CSV de escenario, la guarda de seguridad (host local e identidad de prueba) y el
bucle de publicación con un transporte falso. Ninguna prueba abre una conexión."""

import hashlib
import importlib.util
import json
import queue
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.infrastructure.mqtt.contrato_v1 import TelemetriaV1

REPO = Path(__file__).resolve().parents[2]
RUTA = REPO / "infra-local/scripts/publicador_prueba_mqtt.py"
DOC = REPO / "docs/ml/11_DATOS_DE_PRUEBA_Y_CORRIDAS.md"
spec = importlib.util.spec_from_file_location("publicador_prueba_mqtt", RUTA)
pub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pub  # las dataclasses del script resuelven sus anotaciones por el módulo
spec.loader.exec_module(pub)

INICIO = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
CAMPOS_CONTRATO = set(TelemetriaV1.model_fields) - {"simulated"}


def _planificar(filas, **extra):
    lecturas = pub.lecturas_de_escenario(
        filas, extra.pop("intervalo_s", 10.0), time_quality=extra.pop("time_quality", "sntp_synced"),
        sin_sht31=extra.pop("sin_sht31", False), sin_mc38=extra.pop("sin_mc38", False),
    )
    return pub.planificar(
        lecturas, device_id="PRUEBA_P13_01", boot_id=7, seq_inicial=1, uptime_inicial=600, t0=INICIO, **extra
    )


# ── Mensaje ──────────────────────────────────────────────────────────────


def test_el_mensaje_cumple_el_contrato_y_nunca_lleva_simulated():
    mensaje = _planificar(pub.escenario_valores([5.0]))[0]
    datos = json.loads(mensaje.cuerpo)
    assert "simulated" not in datos
    assert set(datos) == CAMPOS_CONTRATO
    t = TelemetriaV1.model_validate(datos)
    assert t.simulated is None and t.temp_ds18b20 == 5.0 and t.ds18b20_status == "OK"
    assert datos["captured_at"] == "2026-11-14T10:00:00Z" and datos["time_quality"] == "sntp_synced"
    assert mensaje.error_contrato is None


def test_falla_del_ds18b20_es_null_con_sensor_fault_coherente():
    datos = _planificar(pub.escenario_valores([None]))[0].payload
    assert datos["temp_ds18b20"] is None and datos["ds18b20_status"] == "SENSOR_FAULT"
    assert datos["ds18b20_fault"] == "no_response" and datos["sensor_fault"] is True
    assert TelemetriaV1.model_validate(datos).temp_ds18b20 is None


def test_sin_hora_sin_sht31_y_sin_mc38_respetan_las_reglas_cruzadas():
    datos = _planificar(
        pub.escenario_valores([5.0]), time_quality="unsynced", sin_sht31=True, sin_mc38=True
    )[0].payload
    assert datos["captured_at"] is None and datos["time_quality"] == "unsynced"
    assert datos["temp_sht31"] is None and datos["sht31_status"] == "SENSOR_FAULT" and datos["sensor_fault"] is True
    assert datos["door_open"] is None and datos["door_open_s"] is None and datos["mc38_status"] == "not_installed"
    assert datos["temp_ds18b20"] == 5.0  # el DS18B20 sigue válido
    TelemetriaV1.model_validate(datos)


def test_horas_exactas_identidad_y_uptime_consecutivos():
    mensajes = _planificar(pub.escenario_valores([1.9, 2.0, 8.0, 8.1]))
    assert [m.payload["temp_ds18b20"] for m in mensajes] == [1.9, 2.0, 8.0, 8.1]
    assert [m.payload["captured_at"] for m in mensajes] == [
        "2026-11-14T10:00:00Z", "2026-11-14T10:00:10Z", "2026-11-14T10:00:20Z", "2026-11-14T10:00:30Z",
    ]
    assert [(m.boot_id, m.seq_no) for m in mensajes] == [(7, 1), (7, 2), (7, 3), (7, 4)]
    assert [m.payload["uptime_s"] for m in mensajes] == [600, 610, 620, 630]


def test_un_valor_fuera_del_contrato_se_rechaza_salvo_permitir_invalidos():
    with pytest.raises(pub.ErrorEntrada, match="no cumple el contrato"):
        _planificar(pub.escenario_valores([130.0]))
    mensaje = _planificar(pub.escenario_valores([130.0]), permitir_invalidos=True)[0]
    assert mensaje.payload["temp_ds18b20"] == 130.0
    assert "temp_ds18b20" in mensaje.error_contrato


# ── Escenarios ───────────────────────────────────────────────────────────


def test_excursion_base_subida_meseta_bajada_y_cola():
    t = pub.Tramos(base=2, subida=2, evento=2, bajada=1, cola=1, temp=5.0, pico=9.0)
    assert [f["temp_ds18b20"] for f in pub.escenario_excursion(t)] == [5.0, 5.0, 7.0, 9.0, 9.0, 9.0, 5.0, 5.0]


def test_rampa_incluye_los_dos_extremos():
    assert [f["temp_ds18b20"] for f in pub.escenario_rampa(5, 5.0, 9.0)] == [5.0, 6.0, 7.0, 8.0, 9.0]
    with pytest.raises(pub.ErrorEntrada):
        pub.escenario_rampa(1, 5.0, 9.0)


def test_puerta_abierta_lleva_segundos_del_ciclo_y_racha():
    filas = pub.escenario_puerta_abierta(pub.Tramos(base=1, evento=3, cola=1, temp=5.0, pico=7.0), 10.0)
    mensajes = _planificar(filas)
    puertas = [(m.payload["door_open"], m.payload["door_open_s"], m.payload["door_open_streak_s"]) for m in mensajes]
    assert puertas == [(False, 0, None), (True, 10, 10), (True, 10, 20), (True, 10, 30), (False, 0, None)]
    assert [m.payload["temp_ds18b20"] for m in mensajes] == [5.0, 7.0, 7.0, 7.0, 5.0]


def test_falla_de_sensor_solo_durante_el_evento():
    filas = pub.escenario_falla_sensor(pub.Tramos(base=1, evento=2, cola=1), "crc_error")
    estados = [(m.payload["ds18b20_status"], m.payload["ds18b20_fault"]) for m in _planificar(filas)]
    assert estados == [("OK", None), ("SENSOR_FAULT", "crc_error"), ("SENSOR_FAULT", "crc_error"), ("OK", None)]


def test_horas_futuras_se_rechazan():
    mensajes = _planificar(pub.escenario_valores([5.0, 5.0]))
    with pytest.raises(pub.ErrorEntrada, match="hora futura"):
        pub.validar_horas(mensajes, INICIO - timedelta(minutes=5))
    pub.validar_horas(mensajes, INICIO + timedelta(seconds=10))


# ── CSV de escenario ─────────────────────────────────────────────────────


def _csv(tmp_path, texto):
    ruta = tmp_path / "escenario.csv"
    ruta.write_text(texto, encoding="utf-8")
    return ruta


def test_csv_con_horas_absolutas_falla_y_puerta(tmp_path):
    ruta = _csv(tmp_path, (
        "captured_at,temp_ds18b20,door_open,door_open_s,door_open_streak_s,time_quality\n"
        "2026-11-14T10:00:00Z,5.0,,,,\n"
        "2026-11-14T10:00:07Z,8.5,true,7,7,\n"
        ",4.9,,,,unsynced\n"
        "2026-11-14T10:00:30Z,null,,,,\n"
    ))
    lecturas = pub.leer_csv(ruta, 10.0)
    mensajes = pub.planificar(
        lecturas, device_id="PRUEBA_P13_01", boot_id=1, seq_inicial=1, uptime_inicial=0,
        t0=datetime(2030, 1, 1, tzinfo=timezone.utc),  # con horas exactas t0 no cambia captured_at
    )
    datos = [m.payload for m in mensajes]
    assert [d["captured_at"] for d in datos] == [
        "2026-11-14T10:00:00Z", "2026-11-14T10:00:07Z", None, "2026-11-14T10:00:30Z",
    ]
    assert datos[1]["door_open"] is True and datos[1]["door_open_streak_s"] == 7
    assert datos[2]["time_quality"] == "unsynced"
    assert datos[3]["ds18b20_status"] == "SENSOR_FAULT"
    for d in datos:
        TelemetriaV1.model_validate(d)


@pytest.mark.parametrize(
    ("texto", "error"),
    [
        ("temp_ds18b20,simulated\n5.0,true\n", "simulated"),
        ("temp_ds18b20,temperatura\n5.0,1\n", "columnas desconocidas"),
        ("offset_s,captured_at,temp_ds18b20\n0,2026-11-14T10:00:00Z,5.0\n", "no ambas"),
        ("captured_at,temp_ds18b20\n,5.0\n", "solo con time_quality=unsynced"),
        ("captured_at,temp_ds18b20\n2026-11-14T10:00:00,5.0\n", "sin zona"),
        ("offset_s,temp_ds18b20\n0,\n", "temp_ds18b20 vacía"),
        ("offset_s,temp_ds18b20\n20,5.0\n10,5.0\n", "orden de captura"),
    ],
)
def test_csv_invalido_no_publica(tmp_path, texto, error):
    with pytest.raises(pub.ErrorEntrada, match=error):
        pub.leer_csv(_csv(tmp_path, texto), 10.0)


# ── Guarda de seguridad ──────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "[::1]", "LOCALHOST"])
def test_localhost_con_dispositivo_de_prueba_se_permite(host):
    pub.validar_destino(host, "PRUEBA_P13_01", permitir_host=False)
    pub.validar_destino(host, "FARM-02-TEST", permitir_host=False)


@pytest.mark.parametrize(
    "device_id", ["ESP32_FARMACIA_01", "ESP32_DEMO_01", "ESP32_BANCO_01", "ESP32_FARMACIA_TEST", "FARMACIA-PRUEBA"]
)
def test_identidades_de_produccion_se_rechazan_incluso_en_localhost(device_id):
    with pytest.raises(pub.ErrorEntrada, match="producción"):
        pub.validar_destino("localhost", device_id, permitir_host=False)


@pytest.mark.parametrize("device_id", ["NODO_01", "CONTESTADOR_01", "ESP32_LAB_01", "a/b"])
def test_un_device_id_que_no_es_de_prueba_se_rechaza(device_id):
    with pytest.raises(pub.ErrorEntrada):
        pub.validar_destino("localhost", device_id, permitir_host=False)


def test_un_host_remoto_exige_permitir_host():
    with pytest.raises(pub.ErrorEntrada, match="--permitir-host"):
        pub.validar_destino("192.168.18.21", "PRUEBA_P13_01", permitir_host=False)
    pub.validar_destino("192.168.18.21", "PRUEBA_P13_01", permitir_host=True)
    pub.validar_destino("pc-banco.local", "PRUEBA_P13_01", permitir_host=True)
    pub.validar_destino("emqx", "PRUEBA_P13_01", permitir_host=True)


@pytest.mark.parametrize(
    "host",
    [
        "abc123.ala.us-east-1.emqxsl.com", "broker.emqx.io", "coldcare.up.railway.app", "x.proxy.rlwy.net",
        # Red privada de Railway: `.internal` la aceptaría como red local (la base de producción vive ahí).
        "db.railway.internal", "emqx.railway.internal", "railway.internal",
    ],
)
def test_brokers_en_la_nube_se_rechazan_siempre(host):
    with pytest.raises(pub.ErrorEntrada, match="nube"):
        pub.validar_destino(host, "PRUEBA_P13_01", permitir_host=True)


@pytest.mark.parametrize("host", ["8.8.8.8", "broker.ejemplo.com", "100.64.0.1"])
def test_permitir_host_no_cubre_direcciones_publicas(host):
    with pytest.raises(pub.ErrorEntrada, match="red local"):
        pub.validar_destino(host, "PRUEBA_P13_01", permitir_host=True)


def test_internal_que_no_es_de_railway_sigue_siendo_red_local():
    pub.validar_destino("host.docker.internal", "PRUEBA_P13_01", permitir_host=True)


# ── Publicación ──────────────────────────────────────────────────────────


class TransporteFalso:
    def __init__(self, estado="committed", huella_correcta=True, responde=True):
        self.publicados, self.cerrado = [], False
        self.estado, self.huella_correcta, self.responde = estado, huella_correcta, responde

    def publicar(self, topico, cuerpo):
        self.publicados.append((topico, cuerpo))

    def esperar_acuse(self, boot_id, seq_no, timeout_s):
        if not self.responde:
            return None
        cuerpo = next(c for _, c in self.publicados if json.loads(c)["seq_no"] == seq_no)
        huella = hashlib.sha256(cuerpo).hexdigest() if self.huella_correcta else "0" * 64
        return {"device_id": "PRUEBA_P13_01", "boot_id": boot_id, "seq_no": seq_no, "status": self.estado,
                "content_sha256": huella}

    def cerrar(self):
        self.cerrado = True


def test_publica_en_el_topico_del_dispositivo_y_correlaciona_el_acuse():
    mensajes = _planificar(pub.escenario_valores([5.0, 8.5]))
    transporte = TransporteFalso()
    resultados = pub.publicar_todos(
        mensajes, transporte, "PRUEBA_P13_01", tiempo_real=False, pausa_s=0, esperar_acuse=True, timeout_s=1,
    )
    assert [t for t, _ in transporte.publicados] == ["farmacias/PRUEBA_P13_01/lecturas"] * 2
    assert all(b"simulated" not in cuerpo for _, cuerpo in transporte.publicados)
    assert [r.estado for r in resultados] == ["committed", "committed"]
    assert all(r.huella_ok for r in resultados)
    assert pub.cumple(resultados, {"committed"})


@pytest.mark.parametrize(
    ("transporte", "esperados", "ok"),
    [
        (TransporteFalso(estado="rejected"), {"committed"}, False),
        (TransporteFalso(estado="rejected"), {"rejected"}, True),
        (TransporteFalso(huella_correcta=False), {"committed"}, False),
        (TransporteFalso(responde=False), {"committed"}, False),
    ],
)
def test_el_veredicto_exige_estado_esperado_y_huella(transporte, esperados, ok):
    resultados = pub.publicar_todos(
        _planificar(pub.escenario_valores([5.0])), transporte, "PRUEBA_P13_01",
        tiempo_real=False, pausa_s=0, esperar_acuse=True, timeout_s=0.01,
    )
    assert pub.cumple(resultados, esperados) is ok


class TransporteConRetardo:
    """Backend que acusa `retardo_s` después de cada publicación, como tras el COMMIT con la inferencia
    colgada (D08: `IA_PRUEBA_RETARDO_S=2`, timeout 1,0 s → acuse a ~1,2 s). Respeta el plazo que se le
    pide y avanza el reloj falso lo que habría bloqueado. `nunca` son los seq_no sin acuse."""

    def __init__(self, reloj, retardo_s, nunca=()):
        self.reloj, self.retardo, self.nunca = reloj, timedelta(seconds=retardo_s), set(nunca)
        self.publicados = []  # (instante, seq_no, cuerpo)

    def publicar(self, topico, cuerpo):
        self.publicados.append((self.reloj["ahora"], json.loads(cuerpo)["seq_no"], cuerpo))

    def esperar_acuse(self, boot_id, seq_no, timeout_s):
        instante, cuerpo = next((t, c) for t, s, c in self.publicados if s == seq_no)
        limite = self.reloj["ahora"] + timedelta(seconds=timeout_s)
        listo = instante + self.retardo
        if seq_no in self.nunca or listo > limite:
            self.reloj["ahora"] = limite
            return None
        self.reloj["ahora"] = max(self.reloj["ahora"], listo)
        return {"boot_id": boot_id, "seq_no": seq_no, "status": "committed",
                "content_sha256": hashlib.sha256(cuerpo).hexdigest()}

    def cerrar(self):
        pass


def _reloj_falso():
    reloj = {"ahora": INICIO}

    def dormir(segundos):
        reloj["ahora"] += timedelta(seconds=segundos)

    return reloj, dormir


def test_d08_en_tiempo_real_la_lectura_siguiente_no_espera_el_acuse_de_la_anterior():
    mensajes = _planificar(pub.escenario_valores([5.0, 8.5]), intervalo_s=0.5)
    reloj, dormir = _reloj_falso()
    transporte = TransporteConRetardo(reloj, retardo_s=1.2)
    resultados = pub.publicar_todos(
        mensajes, transporte, "PRUEBA_P13_01", tiempo_real=True, pausa_s=0, esperar_acuse=True, timeout_s=8,
        reloj=lambda: reloj["ahora"], dormir=dormir,
    )
    # 8,5 °C sale a t0 + 0,5 s, la hora de su captured_at, no detrás del acuse de 5,0 °C (t0 + 1,2 s).
    assert [t for t, _, _ in transporte.publicados] == [INICIO, INICIO + timedelta(seconds=0.5)]
    assert [m.payload["captured_at"] for m in mensajes] == ["2026-11-14T10:00:00Z", "2026-11-14T10:00:00.500Z"]
    assert [(r.mensaje.seq_no, r.estado, r.huella_ok) for r in resultados] == [(1, "committed", True),
                                                                               (2, "committed", True)]
    assert [r.publicado_en for r in resultados] == [INICIO, INICIO + timedelta(seconds=0.5)]


def test_en_tiempo_real_un_acuse_ausente_vence_en_su_plazo_sin_frenar_los_demas():
    mensajes = _planificar(pub.escenario_valores([5.0, 5.0, 5.0]), intervalo_s=1.0)
    reloj, dormir = _reloj_falso()
    transporte = TransporteConRetardo(reloj, retardo_s=1.5, nunca={2})
    impresos = []
    resultados = pub.publicar_todos(
        mensajes, transporte, "PRUEBA_P13_01", tiempo_real=True, pausa_s=0, esperar_acuse=True, timeout_s=3,
        reloj=lambda: reloj["ahora"], dormir=dormir, al_publicar=lambda r: impresos.append(r.mensaje.seq_no),
    )
    assert [t for t, _, _ in transporte.publicados] == [INICIO + timedelta(seconds=s) for s in (0, 1, 2)]
    assert [r.estado for r in resultados] == ["committed", "sin_acuse", "committed"]
    assert impresos == [1, 2, 3]  # en orden de publicación
    assert reloj["ahora"] == INICIO + timedelta(seconds=4)  # plazo del 2 (1 + 3 s); el 3 ya había llegado
    assert not pub.cumple(resultados, {"committed"})


def test_con_horas_exactas_cada_mensaje_espera_su_acuse_antes_del_siguiente():
    mensajes = _planificar(pub.escenario_valores([5.0, 5.0]))
    reloj, dormir = _reloj_falso()
    transporte = TransporteConRetardo(reloj, retardo_s=1.2)
    pub.publicar_todos(
        mensajes, transporte, "PRUEBA_P13_01", tiempo_real=False, pausa_s=0.2, esperar_acuse=True, timeout_s=8,
        reloj=lambda: reloj["ahora"], dormir=dormir,
    )
    assert [t for t, _, _ in transporte.publicados] == [INICIO, INICIO + timedelta(seconds=1.4)]


def test_transporte_paho_con_plazo_cero_recoge_los_acuses_ya_recibidos():
    """Sin abrir conexión: solo el buffer de acuses. Antes, con plazo 0 devolvía None aunque el acuse
    ya estuviera en la cola, y en tiempo real se habría dado por perdido."""
    transporte = object.__new__(pub.TransportePaho)
    transporte._acuses, transporte._pendientes = queue.Queue(), {}
    for seq in (2, 1):  # llegan en otro orden
        transporte._acuses.put({"boot_id": 7, "seq_no": seq, "status": "committed"})
    assert transporte.esperar_acuse(7, 1, 0)["seq_no"] == 1
    assert transporte.esperar_acuse(7, 2, 0)["seq_no"] == 2  # quedó guardado
    assert transporte.esperar_acuse(7, 3, 0) is None


def test_en_tiempo_real_cada_lectura_sale_en_su_hora_de_captura():
    mensajes = _planificar(pub.escenario_valores([5.0, 5.0, 5.0]))
    reloj = {"ahora": INICIO}
    esperas = []

    def dormir(segundos):
        esperas.append(segundos)
        reloj["ahora"] += timedelta(seconds=segundos)

    pub.publicar_todos(
        mensajes, TransporteFalso(), "PRUEBA_P13_01", tiempo_real=True, pausa_s=0, esperar_acuse=False,
        timeout_s=1, reloj=lambda: reloj["ahora"], dormir=dormir,
    )
    assert esperas == [10.0, 10.0]


# ── main ─────────────────────────────────────────────────────────────────


def _fabrica_prohibida(*_args, **_kwargs):
    raise AssertionError("no debía conectarse")


@pytest.mark.parametrize(
    "argv",
    [
        ["en_rango", "--device-id", "ESP32_FARMACIA_01"],
        ["en_rango", "--host", "abc.emqxsl.com", "--permitir-host"],
        ["en_rango", "--host", "192.168.1.10"],
        ["valores", "--valores", "130", "--inicio", "2026-11-14T10:00:00Z"],
    ],
)
def test_main_no_lee_el_token_ni_conecta_si_la_guarda_lo_impide(argv, tmp_path, capsys):
    token = tmp_path / "t.token"
    token.write_text("secreto-de-prueba", encoding="utf-8")
    codigo = pub.main([*argv, "--token-archivo", str(token)], transporte_factory=_fabrica_prohibida)
    assert codigo == 2
    assert "No se publica nada" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["en_rango", "--inicio", "foo"],
        ["en_rango", "--inicio", "2026-13-40T10:00:00Z"],
        ["en_rango", "--hueco-s", "300", "--inicio", "2026-11-14T10:00:00Z"],
        ["en_rango", "--hueco-s", "-1"],
        ["en_rango", "--hueco-s", "nan"],
    ],
)
def test_main_entrada_invalida_sale_con_2_sin_traza(argv, tmp_path, capsys):
    token = tmp_path / "t.token"
    token.write_text("secreto-de-prueba", encoding="utf-8")
    codigo = pub.main([*argv, "--token-archivo", str(token)], transporte_factory=_fabrica_prohibida)
    assert codigo == 2
    err = capsys.readouterr().err
    assert "No se publica nada" in err and "Traceback" not in err


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("sin CONNACK de localhost:8883 en 10 s"),
        ssl.SSLCertVerificationError(1, "certificate verify failed"),
        FileNotFoundError(2, "No such file or directory", "certs/ca.crt"),
        ConnectionRefusedError(61, "Connection refused"),
        ValueError("Invalid port number."),
    ],
)
def test_main_un_fallo_al_conectar_sale_con_2_y_no_imprime_el_token(error, tmp_path, capsys):
    token = tmp_path / "t.token"
    token.write_text("secreto-de-prueba", encoding="utf-8")

    def fabrica(*_args, **_kwargs):
        raise error

    codigo = pub.main(["en_rango", "--lecturas", "1", "--token-archivo", str(token)], transporte_factory=fabrica)
    assert codigo == 2
    salida = capsys.readouterr()
    assert "No se publica nada" in salida.err and "no se pudo conectar" in salida.err
    assert "secreto-de-prueba" not in salida.out + salida.err


def test_main_hueco_retrasa_la_primera_lectura_como_un_reinicio(capsys):
    antes = datetime.now(timezone.utc)
    codigo = pub.main(
        ["en_rango", "--lecturas", "2", "--hueco-s", "330", "--uptime-inicial", "0", "--dry-run"],
        transporte_factory=_fabrica_prohibida,
    )
    assert codigo == 0
    lineas = [json.loads(linea) for linea in capsys.readouterr().out.splitlines()]
    horas = [datetime.fromisoformat(d["captured_at"].replace("Z", "+00:00")) for d in lineas]
    assert antes + timedelta(seconds=329) <= horas[0] <= datetime.now(timezone.utc) + timedelta(seconds=330)
    assert horas[1] - horas[0] == timedelta(seconds=10)
    assert [d["uptime_s"] for d in lineas] == [0, 10] and [d["seq_no"] for d in lineas] == [1, 2]


def test_main_con_hueco_no_conecta_hasta_el_final_del_hueco(tmp_path, capsys):
    """Un nodo reiniciado no está conectado durante el hueco: la sesión MQTT se abre después."""
    token = tmp_path / "t.token"
    token.write_text("secreto-de-prueba", encoding="utf-8")
    eventos = []

    def fabrica(*_args):
        eventos.append("conectar")
        return TransporteFalso()

    def dormir(segundos):
        eventos.append(("dormir", round(segundos)))

    codigo = pub.main(
        ["en_rango", "--lecturas", "1", "--hueco-s", "330", "--uptime-inicial", "0", "--token-archivo", str(token)],
        transporte_factory=fabrica, dormir=dormir,
    )
    assert codigo == 0
    assert eventos[1] == "conectar" and eventos[0][0] == "dormir"
    assert 320 <= eventos[0][1] <= 326  # 330 s menos el margen de conexión (5 s)
    assert "Hueco: 330 s sin conexión ni publicaciones" in capsys.readouterr().out


def test_main_dry_run_imprime_mensajes_sin_conectar(capsys):
    codigo = pub.main(
        ["valores", "--valores", "1.9,8.1", "--inicio", "2026-11-14T10:00:00Z", "--boot-id", "3", "--dry-run"],
        transporte_factory=_fabrica_prohibida,
    )
    assert codigo == 0
    lineas = [json.loads(linea) for linea in capsys.readouterr().out.splitlines()]
    assert [(d["boot_id"], d["seq_no"], d["temp_ds18b20"]) for d in lineas] == [(3, 1, 1.9), (3, 2, 8.1)]


def test_main_publica_con_el_token_sin_imprimirlo_y_deja_registro(tmp_path, capsys):
    token = tmp_path / "t.token"
    token.write_text("secreto-de-prueba\n", encoding="utf-8")
    registro = tmp_path / "registro.csv"
    recibido = {}

    def fabrica(host, puerto, device_id, token_leido, ca):
        recibido.update(host=host, puerto=puerto, device_id=device_id, token=token_leido)
        recibido["transporte"] = TransporteFalso()
        return recibido["transporte"]

    inicio = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    codigo = pub.main(
        ["excursion", "--base", "1", "--evento", "1", "--cola", "1", "--inicio", inicio, "--pausa-s", "0",
         "--token-archivo", str(token), "--registro", str(registro)],
        transporte_factory=fabrica,
    )
    assert codigo == 0
    assert recibido["token"] == "secreto-de-prueba" and recibido["host"] == "localhost"
    assert recibido["transporte"].cerrado
    salida = capsys.readouterr()
    assert "secreto-de-prueba" not in salida.out + salida.err
    filas = registro.read_text(encoding="utf-8").splitlines()
    assert len(filas) == 4 and filas[0].startswith("boot_id,seq_no,captured_at")
    assert filas[0].endswith(",publicado_en")
    assert "8.5" in filas[2] and "committed" in filas[2]


# ── Ejemplos del documento ───────────────────────────────────────────────


def _csv_del_documento(nombre):
    texto = DOC.read_text(encoding="utf-8")
    bloque = re.search(rf"`{re.escape(nombre)}`[^\n]*\n\n```csv\n(.*?)```", texto, re.DOTALL)
    assert bloque, f"{nombre} no está en {DOC.name}"
    return bloque.group(1)


def test_el_csv_de_d12_del_documento_se_publica_tal_cual(tmp_path):
    ruta = _csv(tmp_path, _csv_del_documento("d12_unsynced.csv"))
    assert pub.main(["csv", "--archivo", str(ruta), "--dry-run"], transporte_factory=_fabrica_prohibida) == 0
    mensajes = pub.planificar(
        pub.leer_csv(ruta, 10.0), device_id="PRUEBA_P13_01", boot_id=1, seq_inicial=1, uptime_inicial=0, t0=INICIO
    )
    assert all(m.error_contrato is None and m.payload["time_quality"] == "unsynced" for m in mensajes)
    assert mensajes[-1].payload["temp_ds18b20"] == 8.5


def test_el_csv_de_d19_del_documento_solo_trae_negativos_que_el_backend_rechaza(tmp_path, capsys):
    ruta = _csv(tmp_path, _csv_del_documento("d19_implausibles.csv"))
    # Sin --permitir-invalidos no sale nada; con él se puede publicar y esperar `rejected` en todas.
    assert pub.main(["csv", "--archivo", str(ruta), "--dry-run"], transporte_factory=_fabrica_prohibida) == 2
    assert pub.main(
        ["csv", "--archivo", str(ruta), "--permitir-invalidos", "--esperado", "rejected", "--dry-run"],
        transporte_factory=_fabrica_prohibida,
    ) == 0
    mensajes = pub.planificar(
        pub.leer_csv(ruta, 10.0), device_id="PRUEBA_P13_01", boot_id=1, seq_inicial=1, uptime_inicial=0,
        t0=INICIO, permitir_invalidos=True,
    )
    assert len(mensajes) == 4 and all(m.error_contrato for m in mensajes)
