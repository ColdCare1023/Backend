"""HU-17 esc. 1 (P0-4, arreglo A): el servicio de inferencia SIRVE el contexto
del SHT31.

Antes, un manifiesto `termico_ambiente` cargaba (solo se comparaban los
nombres de las features), pero `features_en_instante` no recibía la
temperatura ambiental ni la humedad y `predecir` solo enviaba temperatura y
puerta: toda lectura quedaba «Sin clasificación IA» con el motivo engañoso
`ventana_15min_sin_cobertura_suficiente`, aunque el historial alcanzara.

Ahora:
- el servicio construye las señales que pide el conjunto (puerta, SHT31) con
  las mismas filas, la misma rejilla y la misma segmentación que el
  entrenamiento, y clasifica con el vector del entrenamiento;
- si la temperatura interna alcanza pero falta el SHT31, el motivo es
  `ventana_15min_sin_contexto_ambiental`;
- un esquema que el servicio no sabe calcular no carga (`incompatible`) ni se
  activa (`esquema_features_no_servible`).
"""

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
import sklearn
from sklearn.ensemble import RandomForestClassifier

from src.domain.entities.lectura_termica import LecturaTermica
from src.domain.value_objects.motivo_bloqueo import codigo_de
from src.infrastructure.ai.prediccion_preventiva import calidad, registro
from src.infrastructure.ai.prediccion_preventiva import servicio as modulo_servicio
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    CALIDAD_HISTORIAL_INSUFICIENTE,
    CALIDAD_OK,
    ESTADO_APROBADO,
    ESTADO_VALIDADO_OFFLINE,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import (
    CANAL_PUERTA,
    CANALES_POR_CONJUNTO,
    CONJUNTO_TERMICO,
    CONJUNTO_TERMICO_AMBIENTE,
    CONJUNTO_TERMICO_PUERTA,
    CONJUNTOS,
    features_de_rejillas,
    nombres_features,
)
from src.infrastructure.ai.prediccion_preventiva.ingesta import a_epoch_ns
from src.infrastructure.ai.prediccion_preventiva.servicio import (
    ACTIVACION_ESQUEMA_NO_SERVIBLE,
    ACTIVACION_MANIFIESTO_DISTINTO_DEL_HISTORIAL,
    CODIGO_INCOMPATIBLE,
    MODO_ACTIVO,
    MODO_SOMBRA,
    ServicioPrediccionPreventiva,
    motivo_esquema_no_servible,
)
from tests.unit._preventivo_sintetico import lecturas, por_minuto

CONFIG = ConfiguracionPreventiva()
AHORA = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
# Contrato entre pistas (backend y frontend): el texto exacto del motivo.
MOTIVO_SIN_CONTEXTO = "ventana_15min_sin_contexto_ambiental"
MOTIVO_SIN_COBERTURA = "ventana_15min_sin_cobertura_suficiente"


def _version(conjunto: str) -> str:
    return f"rf-hu17-{conjunto.replace('_', '-')}-1.0.0"


def _registrar(raiz, conjunto: str, *, features: list[str] | None = None, aprobar: bool = False) -> str:
    """Registra con la CLI (registrar y estado, que anotan historial.json) un
    bosque de juguete para `conjunto`. Devuelve el SHA-256 del artefacto."""
    version = _version(conjunto)
    nombres = features if features is not None else nombres_features(CONFIG, conjunto)
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, (200, len(nombres)))
    modelo = RandomForestClassifier(n_estimators=5, random_state=0).fit(x, (x[:, 0] > 0).astype(int))
    registro.registrar(modelo, {
        "version": version, "estado": "EXPERIMENTAL", "conjunto_features": conjunto, "features": nombres,
        "configuracion": CONFIG.como_dict(), "umbral_decision": 0.5, "puntuacion_calibrada": False,
        "versiones": {"scikit_learn": sklearn.__version__},
    }, raiz)
    if aprobar:
        registro.cambiar_estado(version, ESTADO_VALIDADO_OFFLINE, "prueba", "qa", None, raiz)
        evidencia = raiz / f"exp_d_{version}.json"
        evidencia.write_text("{}", encoding="utf-8")
        registro.cambiar_estado(version, ESTADO_APROBADO, "prueba", "qa", evidencia, raiz)
    return registro.leer_manifiesto(version, raiz)["artefacto"]["sha256"]


def _servicio(raiz, conjunto: str) -> ServicioPrediccionPreventiva:
    sha = _registrar(raiz, conjunto)
    return ServicioPrediccionPreventiva(MODO_SOMBRA, _version(conjunto), sha, raiz=raiz)


def _lectura(t: datetime, temp: float | None = 5.0, ambiente: float | None = 22.0,
             humedad: float | None = 55.0, puerta: bool | None = False) -> LecturaTermica:
    return LecturaTermica(
        device_id="ESP32-HU17", timestamp=t, temperatura_ambiental=ambiente, humedad_ambiental=humedad,
        temperatura_interna=temp, apertura_refrigerador=puerta, estado_conectividad="online",
        time_quality="synced",
    )


def _historial(minutos: int = 40, paso_s: int = 30, sin_sht31_ultimos_s: int = 0, falta: str = "ambos"):
    """Lecturas estables (5,0 °C) anteriores a AHORA. Las de los últimos
    `sin_sht31_ultimos_s` segundos llegan sin temperatura ambiental, sin
    humedad o sin ninguna de las dos (`falta`)."""
    n = minutos * 60 // paso_s
    filas = []
    for i in range(n, 0, -1):
        t = AHORA - timedelta(seconds=paso_s * i)
        sin = paso_s * i <= sin_sht31_ultimos_s
        filas.append(_lectura(
            t,
            ambiente=None if sin and falta in ("ambiente", "ambos") else 22.0,
            humedad=None if sin and falta in ("humedad", "ambos") else 55.0,
        ))
    return filas


# ── El conjunto con SHT31 clasifica ────────────────────────────────────────


@pytest.mark.parametrize("conjunto", CONJUNTOS)
def test_con_40_min_de_historial_todo_conjunto_clasifica(tmp_path, conjunto):
    """Sonda del R2 (`probe_servicio_ambiente.py`): con 40 min de historial
    `termico_ambiente` quedaba en HISTORIAL_INSUFICIENTE; ahora clasifica,
    igual que los otros dos conjuntos."""
    p = _servicio(tmp_path, conjunto).predecir(_lectura(AHORA), _historial())

    assert (p.estado, p.motivo) == (CALIDAD_OK, None)
    assert list(p.vector_features) == nombres_features(CONFIG, conjunto)
    if conjunto == CONJUNTO_TERMICO_AMBIENTE:
        assert p.vector_features["temp_amb_actual"] == 22.0
        assert p.vector_features["humedad_actual"] == 55.0
        assert p.vector_features["dif_interna_ambiente"] == pytest.approx(5.0 - 22.0)
        assert p.vector_features["media_temp_amb_15m"] == 22.0


@pytest.mark.parametrize("falta", ["ambos", "ambiente", "humedad"])
def test_sin_sht31_el_motivo_es_el_contexto_ambiental(tmp_path, falta):
    """La temperatura interna alcanza (40 min estables) pero el SHT31 no envía
    dato desde hace 2 min: no se predice, y el motivo lo dice."""
    lectura = _lectura(
        AHORA, ambiente=None if falta in ("ambiente", "ambos") else 22.0,
        humedad=None if falta in ("humedad", "ambos") else 55.0,
    )
    p = _servicio(tmp_path, CONJUNTO_TERMICO_AMBIENTE).predecir(
        lectura, _historial(sin_sht31_ultimos_s=120, falta=falta)
    )

    assert p.estado == CALIDAD_HISTORIAL_INSUFICIENTE
    assert p.motivo == MOTIVO_SIN_CONTEXTO
    assert p.clase is None and p.puntuacion is None and p.vector_features is None


def test_una_lectura_sin_sht31_dentro_de_la_agregacion_aun_clasifica(tmp_path):
    """Misma regla que el entrenamiento: el punto actual de la rejilla es la
    media de las lecturas de los últimos 90 s, así que una sola lectura sin
    SHT31 se cubre con la anterior (30 s antes)."""
    p = _servicio(tmp_path, CONJUNTO_TERMICO_AMBIENTE).predecir(_lectura(AHORA, ambiente=None, humedad=None), _historial())

    assert p.estado == CALIDAD_OK
    assert p.vector_features["temp_amb_actual"] == 22.0


def test_sin_cobertura_termica_se_conserva_el_motivo_de_siempre(tmp_path):
    """3 min de historial: falta temperatura interna, no el SHT31."""
    p = _servicio(tmp_path, CONJUNTO_TERMICO_AMBIENTE).predecir(_lectura(AHORA), _historial(minutos=3))

    assert (p.estado, p.motivo) == (CALIDAD_HISTORIAL_INSUFICIENTE, MOTIVO_SIN_COBERTURA)


@pytest.mark.parametrize("conjunto", [CONJUNTO_TERMICO, CONJUNTO_TERMICO_PUERTA])
def test_los_conjuntos_sin_sht31_no_lo_exigen(tmp_path, conjunto):
    """El SHT31 solo se lee si el conjunto lo usa: sin él, los otros clasifican."""
    p = _servicio(tmp_path, conjunto).predecir(
        _lectura(AHORA, ambiente=None, humedad=None), _historial(sin_sht31_ultimos_s=40 * 60)
    )

    assert (p.estado, p.motivo) == (CALIDAD_OK, None)


# ── Paridad entrenamiento ↔ servicio, de punta a punta ─────────────────────


def _serie_por_minuto() -> pd.DataFrame:
    """2 h a una lectura por minuto (cada lectura cae en un punto de la
    rejilla) con puerta, SHT31, DS18B20 en falla con la puerta abierta (uno
    corto dentro del segmento y otro de 6 min que corta la serie) y el SHT31
    sin dato durante 6 min."""
    rng = np.random.default_rng(11)
    df = lecturas(por_minuto([(5.0, 5.4, 40), (5.4, 7.6, 30), (7.6, 4.5, 50)]) + rng.normal(0, 0.03, 120))
    df["puerta_abierta"] = (rng.random(120) < 0.15).astype(float)
    df["temperatura_ambiental"] = 22.0 + rng.normal(0, 0.3, 120)
    df["humedad_ambiental"] = 55.0 + rng.normal(0, 1.0, 120)
    for desde, hasta in ((25, 27), (80, 86)):
        df.loc[desde:hasta - 1, ["temperatura_interna", "puerta_abierta", "temperatura_ambiental"]] = [
            np.nan, 1.0, 30.0,
        ]
    df.loc[50:55, ["temperatura_ambiental", "humedad_ambiental"]] = np.nan
    return df


def _a_lecturas(df: pd.DataFrame) -> list[LecturaTermica]:
    def _o_none(v):
        return None if pd.isna(v) else float(v)

    return [
        _lectura(
            fila.timestamp.to_pydatetime(), temp=_o_none(fila.temperatura_interna),
            ambiente=_o_none(fila.temperatura_ambiental), humedad=_o_none(fila.humedad_ambiental),
            puerta=None if pd.isna(fila.puerta_abierta) else bool(fila.puerta_abierta),
        )
        for fila in df.itertuples()
    ]


@pytest.mark.parametrize("conjunto", CONJUNTOS)
def test_paridad_del_servicio_con_el_entrenamiento(tmp_path, conjunto):
    """`ServicioPrediccionPreventiva.predecir`, con el historial que le pasa la
    ingesta (LecturaTermica, None donde un sensor no midió), produce el vector
    y la calidad del pipeline de entrenamiento (`calidad.preparar_rejillas` +
    `features_de_rejillas`) en cada lectura que llega al modelo."""
    df = _serie_por_minuto()
    rejillas = calidad.preparar_rejillas(df, CONFIG)
    x_ent, ok_ent = features_de_rejillas(rejillas, CONFIG, conjunto)
    fila_de = {int(g): i for i, g in enumerate(rejillas["g_ns"].to_numpy())}
    ts_ns = a_epoch_ns(df["timestamp"])  # una lectura por minuto: cada una es un punto de la rejilla
    filas = _a_lecturas(df)
    servicio = _servicio(tmp_path, conjunto)
    nombres = nombres_features(CONFIG, conjunto)
    comparadas = omitidas = 0
    for i, lectura in enumerate(filas):
        if lectura.temperatura_interna is None or lectura.es_excursion_confirmada():
            continue
        p = servicio.predecir(lectura, list(reversed(filas[:i])))
        j = fila_de[int(ts_ns[i])]
        assert (p.estado == CALIDAD_OK) == bool(ok_ent[j]), (i, p.estado, p.motivo)
        if p.estado == CALIDAD_OK:
            np.testing.assert_allclose([p.vector_features[n] for n in nombres], x_ent[j], rtol=0, atol=1e-6)
            comparadas += 1
        else:
            assert p.estado == CALIDAD_HISTORIAL_INSUFICIENTE
            omitidas += 1
    assert comparadas > 50 and omitidas > 10


# ── Un esquema que el servicio no calcula no carga ni se activa ───────────


def test_los_tres_conjuntos_son_servibles_y_se_activan(tmp_path):
    for conjunto in CONJUNTOS:
        sha = _registrar(tmp_path, conjunto, aprobar=True)
        version = _version(conjunto)
        servicio = ServicioPrediccionPreventiva(
            MODO_ACTIVO, version, sha, raiz=tmp_path, version_activa=(version, sha)
        )
        assert servicio.motivos_activacion(version, sha, 30, sklearn.__version__, conjunto) == [], conjunto
        assert servicio.disponible, servicio.motivo_rechazo()
        assert conjunto in CANALES_POR_CONJUNTO


def test_los_modelos_del_repositorio_siguen_siendo_servibles():
    for entrada in registro.listar():
        assert motivo_esquema_no_servible(registro.leer_manifiesto(entrada["version"])) is None, entrada


def test_un_conjunto_que_el_servicio_no_calcula_no_carga_ni_se_activa(tmp_path):
    """Un conjunto que solo existe en el entrenamiento (o en un manifiesto)
    no puede quedar cargado clasificando nada: se rechaza al cargar y al activar."""
    sha = _registrar(tmp_path, "termico_futuro", features=["temp_actual", "otra_senal_actual"], aprobar=True)
    version = _version("termico_futuro")
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, version, sha, raiz=tmp_path, version_activa=(version, sha))

    assert servicio.disponible is False
    assert servicio.motivo_codigo() == CODIGO_INCOMPATIBLE
    assert ACTIVACION_ESQUEMA_NO_SERVIBLE in servicio.motivo_rechazo()
    motivos = servicio.motivos_activacion(version, sha, 30, sklearn.__version__, "termico_futuro")
    assert [codigo_de(m) for m in motivos] == [ACTIVACION_ESQUEMA_NO_SERVIBLE]
    assert motivos[0] == (
        "el servicio de inferencia no puede calcular las features del esquema del manifiesto (termico_futuro)"
    )


def test_un_conjunto_cuyas_senales_no_recibe_el_servicio_no_carga(tmp_path, monkeypatch):
    """Si el servicio dejara de construir una señal (aquí, el SHT31), el
    conjunto que la usa se rechaza en vez de cargar y no clasificar nunca."""
    monkeypatch.setattr(modulo_servicio, "CANALES_SERVIDOS", frozenset({CANAL_PUERTA}))
    sha = _registrar(tmp_path, CONJUNTO_TERMICO_AMBIENTE, aprobar=True)
    version = _version(CONJUNTO_TERMICO_AMBIENTE)
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, version, sha, raiz=tmp_path, version_activa=(version, sha))

    assert servicio.motivo_codigo() == CODIGO_INCOMPATIBLE
    assert [codigo_de(m) for m in servicio.motivos_activacion(version, sha)] == [ACTIVACION_ESQUEMA_NO_SERVIBLE]


def test_features_distintas_de_las_del_extractor_no_se_activan(tmp_path):
    sha = _registrar(tmp_path, CONJUNTO_TERMICO_AMBIENTE, aprobar=True)
    version = _version(CONJUNTO_TERMICO_AMBIENTE)
    ruta = registro.ruta_manifiesto(version, tmp_path)
    manifiesto = json.loads(ruta.read_text(encoding="utf-8"))
    manifiesto["features"] = manifiesto["features"][:-1]
    ruta.write_text(json.dumps(manifiesto), encoding="utf-8")
    servicio = ServicioPrediccionPreventiva(MODO_ACTIVO, version, sha, raiz=tmp_path, version_activa=(version, sha))

    assert [codigo_de(m) for m in servicio.motivos_activacion(version, sha)] == [
        ACTIVACION_MANIFIESTO_DISTINTO_DEL_HISTORIAL, ACTIVACION_ESQUEMA_NO_SERVIBLE,
    ]
    assert servicio.motivo_codigo() == CODIGO_INCOMPATIBLE
