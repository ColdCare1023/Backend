"""Contrato IoT↔backend fijado sobre los BYTES que emite el firmware actual.

Los payloads de este archivo no están escritos a mano: se obtuvieron
compilando `iot-firmware/src/core/PayloadCore.cpp` y `Reloj.cpp` en el host y
ejecutando `core::serializarLectura()`. Es la diferencia que importa — el
proyecto ya se rompió una vez (`duracion_apertura_segundos`) precisamente
porque todas las pruebas del backend construían el payload con los campos que
el backend ya esperaba, en lugar de con los que el nodo realmente publica.

Regenerar tras cambiar el firmware:

    g++ -std=c++17 -I iot-firmware/src/core gen.cpp \\
        iot-firmware/src/core/PayloadCore.cpp iot-firmware/src/core/Reloj.cpp -o gen && ./gen
"""

import json

import pytest
from pydantic import ValidationError

from src.infrastructure.mqtt.payload_schema import MAX_BYTES_PAYLOAD_MQTT, LecturaPayload

# Salida literal del firmware con los tres sensores respondiendo.
PAYLOAD_NOMINAL = (
    '{"device_id":"FARM-01-CDL","timestamp":"2026-07-29T10:34:56Z",'
    '"estado_conectividad":"online","firmware_version":"1.4.0",'
    '"temperatura_interna":4.53,"temperatura_ambiental":5.21,'
    '"humedad_ambiental":62.40,"apertura_refrigerador":true,'
    '"duracion_apertura_segundos":42}'
)

# Salida literal con los tres sensores caídos: `null` explícito, nunca 0.0
# (equivalente edge del defecto B-05; un 0.0 inventado en una cadena de frío de
# 2-8 °C es indistinguible de una excursión real).
PAYLOAD_SENSORES_CAIDOS = (
    '{"device_id":"FARM-01-CDL","timestamp":"2026-07-29T10:35:26Z",'
    '"estado_conectividad":"offline","firmware_version":"1.4.0",'
    '"temperatura_interna":null,"temperatura_ambiental":null,'
    '"humedad_ambiental":null,"apertura_refrigerador":false,'
    '"duracion_apertura_segundos":0}'
)


def test_payload_nominal_del_firmware_se_acepta_tal_cual():
    payload = LecturaPayload.model_validate_json(PAYLOAD_NOMINAL)

    assert payload.device_id == "FARM-01-CDL"
    assert payload.temperatura_interna == pytest.approx(4.53)
    assert payload.temperatura_ambiental == pytest.approx(5.21)
    assert payload.humedad_ambiental == pytest.approx(62.40)
    assert payload.apertura_refrigerador is True
    assert payload.duracion_apertura_segundos == 42
    assert payload.firmware_version == "1.4.0"


def test_el_timestamp_iso8601_con_Z_se_interpreta_como_utc():
    """`core::formatearISO8601` emite `YYYY-MM-DDTHH:MM:SSZ`. Si el backend lo
    tomara como hora local, la ventana de validación de ±2 h rechazaría las
    lecturas de un nodo perfectamente sincronizado (Lima es UTC-5)."""
    payload = LecturaPayload.model_validate_json(PAYLOAD_NOMINAL)

    assert payload.timestamp.tzinfo is not None
    assert payload.timestamp.utcoffset().total_seconds() == 0
    assert payload.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-07-29T10:34:56Z"


def test_sensores_caidos_llegan_como_none_y_no_como_cero():
    payload = LecturaPayload.model_validate_json(PAYLOAD_SENSORES_CAIDOS)

    assert payload.temperatura_interna is None
    assert payload.temperatura_ambiental is None
    assert payload.humedad_ambiental is None
    assert payload.estado_conectividad == "offline"


def test_los_dos_valores_de_conectividad_del_firmware_se_aceptan():
    """El firmware emite exactamente `online` u `offline`
    (`lectura.online ? "online" : "offline"`), no hay un tercer valor."""
    for estado in ("online", "offline"):
        datos = json.loads(PAYLOAD_NOMINAL)
        datos["estado_conectividad"] = estado
        assert LecturaPayload.model_validate(datos).estado_conectividad == estado


def test_los_rangos_del_backend_coinciden_con_los_del_firmware():
    """`RangosSensores.h` convierte a NaN —y por tanto a `null`— todo lo que
    caiga fuera de estos límites, así que el backend nunca debería recibir un
    número fuera de rango. Que ambos lados declaren el MISMO intervalo es lo
    que garantiza que ninguna lectura válida se pierda por un desajuste."""
    limites = {
        # campo: (mínimo, máximo) según la hoja de datos del sensor
        "temperatura_interna": (-55.0, 125.0),   # DS18B20
        "temperatura_ambiental": (-40.0, 125.0),  # SHT31
        "humedad_ambiental": (0.0, 100.0),        # SHT31
    }

    for campo, (minimo, maximo) in limites.items():
        for valor in (minimo, maximo):
            datos = json.loads(PAYLOAD_NOMINAL)
            datos[campo] = valor
            assert LecturaPayload.model_validate(datos) is not None, f"{campo}={valor}"

        for fuera in (minimo - 0.01, maximo + 0.01):
            datos = json.loads(PAYLOAD_NOMINAL)
            datos[campo] = fuera
            with pytest.raises(ValidationError):
                LecturaPayload.model_validate(datos)


def test_el_redondeo_a_dos_decimales_no_saca_ningun_valor_de_rango():
    """El firmware valida el float crudo y luego serializa con `%.2f`. Ese
    redondeo podría, en principio, empujar un valor válido por encima del
    límite; se comprueba en el borde superior de cada sensor."""
    for campo, maximo in (
        ("temperatura_interna", 125.0),
        ("temperatura_ambiental", 125.0),
        ("humedad_ambiental", 100.0),
    ):
        datos = json.loads(PAYLOAD_NOMINAL)
        datos[campo] = float(f"{maximo - 0.001:.2f}")  # lo que emitiría el nodo
        assert LecturaPayload.model_validate(datos) is not None


def test_el_payload_del_firmware_cabe_de_sobra_en_el_techo_mqtt():
    """`PayloadBuilder::build()` descarta cualquier payload de más de 512 B.
    El techo del backend debe quedar por encima con margen, o rechazaría
    mensajes legítimos."""
    assert len(PAYLOAD_NOMINAL.encode()) < 512
    assert MAX_BYTES_PAYLOAD_MQTT > 512


def test_firmware_version_desmesurada_se_rechaza():
    """`devices.firmware_version` es String(20). Sin tope, un `build_flag` mal
    puesto metía una cadena ilimitada en la columna JSONB de cada lectura."""
    datos = json.loads(PAYLOAD_NOMINAL)
    datos["firmware_version"] = "9" * 64

    with pytest.raises(ValidationError):
        LecturaPayload.model_validate(datos)


def test_el_orden_de_los_campos_no_afecta_a_la_validacion():
    """JSON no da significado al orden; el firmware lo mantiene solo para poder
    comparar capturas del monitor serie con la documentación §3.5."""
    datos = json.loads(PAYLOAD_NOMINAL)
    invertido = json.dumps(dict(reversed(list(datos.items()))))

    assert LecturaPayload.model_validate_json(invertido).device_id == "FARM-01-CDL"


def test_ningun_campo_del_firmware_falta_en_el_esquema():
    """Guardia frente a la divergencia que ya ocurrió: si el firmware añade un
    campo y el backend no lo declara, `extra="forbid"` rechaza el 100 % de las
    lecturas. Esta prueba lo convierte en un fallo de CI."""
    emitidos = set(json.loads(PAYLOAD_NOMINAL))
    declarados = set(LecturaPayload.model_fields)
    alias = {"temperatura_sht31", "humedad", "temperatura_ds18b20", "puerta_abierta"}

    assert emitidos <= (declarados | alias), f"sin declarar: {emitidos - declarados - alias}"
