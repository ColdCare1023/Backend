"""HU-17 contra la base: el historial del modelo preventivo se pide por alcance
temporal (`desde`), no por un tope fijo de filas.

Antes, `PredecirRiesgoPreventivoUseCase` pedía las 120 lecturas anteriores a t.
Con un nodo a 5 s (el mínimo que admite HU-54) eso son 600 s: la ventana de
15 min nunca se completaba y toda lectura quedaba HISTORIAL_INSUFICIENTE,
aunque el entrenamiento sí clasificara ese instante."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier

from src.application.use_cases.predecir_riesgo_preventivo import PredecirRiesgoPreventivoUseCase
from src.domain.entities.lectura_termica import LecturaTermica
from src.infrastructure.ai.prediccion_preventiva import calidad, registro
from src.infrastructure.ai.prediccion_preventiva.configuracion import CALIDAD_OK, ConfiguracionPreventiva
from src.infrastructure.ai.prediccion_preventiva.features import (
    CONJUNTO_TERMICO,
    features_de_rejillas,
    nombres_features,
)
from src.infrastructure.ai.prediccion_preventiva.servicio import MODO_SOMBRA, ServicioPrediccionPreventiva
from src.infrastructure.database.repositories.lectura_repository import SQLAlchemyLecturaRepository

CONFIG = ConfiguracionPreventiva()
DEVICE_ID = "FARM-HU17-ALCANCE"
BASE = datetime(2026, 1, 5, tzinfo=timezone.utc)
VERSION = "rf-alcance-bd-1.0.0"


def _utc(valor: datetime) -> datetime:
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


def _lectura(segundo: int, temperatura: float, seq_no: int) -> LecturaTermica:
    return LecturaTermica(
        device_id=DEVICE_ID, timestamp=BASE + timedelta(seconds=segundo), temperatura_ambiental=22.0,
        humedad_ambiental=55.0, temperatura_interna=temperatura, apertura_refrigerador=False,
        estado_conectividad="online", time_quality="synced", boot_id=1, seq_no=seq_no,
    )


def _servicio(raiz) -> ServicioPrediccionPreventiva:
    nombres = nombres_features(CONFIG, CONJUNTO_TERMICO)
    x = np.random.default_rng(0).normal(0, 1, (120, len(nombres)))
    modelo = RandomForestClassifier(n_estimators=3, random_state=0).fit(x, (x[:, 0] > 0).astype(int))
    registro.registrar(modelo, {
        "version": VERSION, "estado": "EXPERIMENTAL", "conjunto_features": CONJUNTO_TERMICO, "features": nombres,
        "configuracion": CONFIG.como_dict(), "umbral_decision": 0.5, "puntuacion_calibrada": False,
        "versiones": {"scikit_learn": sklearn.__version__},
    }, raiz)
    sha = registro.leer_manifiesto(VERSION, raiz)["artefacto"]["sha256"]
    return ServicioPrediccionPreventiva(MODO_SOMBRA, VERSION, sha, raiz=raiz)


async def test_el_repositorio_filtra_desde_inclusive_y_hasta_exclusive(db_session_factory):
    async with db_session_factory() as session:
        repo = SQLAlchemyLecturaRepository(session)
        for minuto in range(10):
            await repo.agregar(_lectura(60 * minuto, 5.0, minuto))
        await session.commit()

    async with db_session_factory() as session:
        repo = SQLAlchemyLecturaRepository(session)
        tramo = await repo.listar_recientes_por_device(
            DEVICE_ID, limite=50, hasta=BASE + timedelta(minutes=8), desde=BASE + timedelta(minutes=3)
        )
        sin_desde = await repo.listar_recientes_por_device(DEVICE_ID, limite=50, hasta=BASE + timedelta(minutes=8))

    assert [_utc(lec.timestamp) for lec in tramo] == [BASE + timedelta(minutes=m) for m in (7, 6, 5, 4, 3)]
    assert len(sin_desde) == 8  # sin `desde`, el comportamiento anterior


async def test_a_5_s_el_modelo_preventivo_recibe_toda_la_ventana(db_session_factory, tmp_path):
    """30 min de lecturas a 5 s persistidas; la lectura de t (un punto de la
    rejilla de 60 s) se predice con el historial que devuelve la base y da el
    vector del entrenamiento."""
    paso_s, n = 5, 30 * 60 // 5
    temperaturas = 4.5 + 0.02 * np.sin(np.arange(n + 1) / 15.0) + 0.0004 * np.arange(n + 1)
    lecturas = [_lectura(paso_s * i, float(temperaturas[i]), i) for i in range(n + 1)]
    async with db_session_factory() as session:
        repo = SQLAlchemyLecturaRepository(session)
        for lectura in lecturas[:-1]:
            await repo.agregar(lectura)
        await session.commit()

    actual = lecturas[-1]  # BASE + 30 min
    async with db_session_factory() as session:
        p = await PredecirRiesgoPreventivoUseCase(
            _servicio(tmp_path), SQLAlchemyLecturaRepository(session), None
        ).predecir(actual)

    df = pd.DataFrame({
        "fuente": "bd", "entorno_id": "e", "serie_id": "S1",
        "timestamp": pd.to_datetime([lec.timestamp for lec in lecturas], utc=True),
        "temperatura_interna": temperaturas, "puerta_abierta": 0.0,
    })
    rejillas = calidad.preparar_rejillas(calidad.limpiar(df)[0], CONFIG)
    x_ent, ok_ent = features_de_rejillas(rejillas, CONFIG, CONJUNTO_TERMICO)
    [j] = np.flatnonzero(rejillas["g_ns"].to_numpy() == pd.Timestamp(actual.timestamp).value)

    assert ok_ent[j]
    assert (p.estado, p.motivo) == (CALIDAD_OK, None)
    np.testing.assert_allclose(
        [p.vector_features[n] for n in nombres_features(CONFIG, CONJUNTO_TERMICO)], x_ent[j], rtol=0, atol=1e-6
    )
