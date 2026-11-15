"""HU-18 P-2 (P0-5 de la revalidación r2), de punta a punta en un registro temporal.

Antes no había una ruta soportada para registrar en `models/preventivo/<V>/` un
modelo entrenado con las corridas: `corridas` solo informaba, y un manifiesto
escrito a mano recibía 409 `manifiesto_distinto_del_historial` en /activar.

Ahora `corridas --registrar --version V`:

1. entrena con el CSV de corridas (formato del exportador P-1) y registra con
   `registro.registrar`: el SHA-256 del artefacto coincide con el manifiesto y
   `historial.json` anota el del manifiesto;
2. el manifiesto lleva lo que piden la carga y /activar (horizonte, conjunto
   y lista de features, umbral, versiones de scikit-learn, Python y numpy) más
   la procedencia de los datos y las corridas de cada partición;
3. la versión carga en sombra tal como quedó registrada (EXPERIMENTAL);
4. aprobada con la CLI `estado`, registrada en la base con el cuerpo que
   imprime la CLI y con el entorno en modo activo, POST /activar responde 200:
   ningún 409 del manifiesto (horizonte, scikit-learn, esquema, historial).

En sombra, /activar valida el artefacto LEGADO (`gobierno.modelo_gobernado`):
el registro de la base aprueba lo que decide el riesgo y en sombra el
preventivo no decide nada. Por eso la activación se ejerce con el entorno en
`activo`, que es cuando /activar contrasta el manifiesto preventivo."""

import json
import os
import platform

import numpy as np
import pytest
import sklearn

from src.infrastructure.ai import random_forest_service as rf
from src.infrastructure.ai.prediccion_preventiva import registro
from src.infrastructure.ai.prediccion_preventiva import servicio as modulo_servicio
from src.infrastructure.ai.prediccion_preventiva.__main__ import main
from src.infrastructure.ai.prediccion_preventiva.configuracion import (
    ESTADO_APROBADO,
    ESTADO_EXPERIMENTAL,
    ConfiguracionPreventiva,
)
from src.infrastructure.ai.prediccion_preventiva.features import nombres_features
from src.infrastructure.ai.prediccion_preventiva.servicio import (
    MODO_ACTIVO,
    MODO_SOMBRA,
    ServicioPrediccionPreventiva,
)
from src.infrastructure.ai.random_forest_service import RandomForestRiesgoService
from src.infrastructure.config import get_settings
from tests.conftest import auth_header
from tests.corridas_sinteticas import escribir_corridas

VERSION = "rf-corridas-h30-termico-0.1.0"
_VARIABLES = ("IA_PREVENTIVO_MODO", "IA_PREVENTIVO_VERSION", "IA_PREVENTIVO_SHA256", "IA_LEGADO_MODO")
# Con datos sintéticos de 2 incidentes de prueba no se alcanzan los criterios
# reales (≥ 10 incidentes evaluables, etc.); para ejercer /activar se registran
# métricas que los cumplen. Las del manifiesto se comprueban aparte.
_METRICAS_QUE_CUMPLEN = {
    "f1_macro": 0.88, "incidentes_evaluables": 12, "sensibilidad_incidente": 0.92,
    "ic95_inferior_sensibilidad": 0.62, "falsas_alarmas_por_dia_sensor": 1.1,
    "mediana_anticipacion_min": 9.0, "ap_random_forest": 0.71, "ap_mejor_baseline": 0.55,
}
_CODIGOS_DEL_MANIFIESTO = {
    "version_preventiva_no_configurada", "version_distinta_de_la_desplegada", "sha256_no_configurado",
    "sha256_distinto_del_registrado", "artefacto_distinto_del_registrado", "manifiesto_ilegible",
    "manifiesto_distinto_del_historial", "manifiesto_no_aprobado", "horizonte_distinto_del_manifiesto",
    "esquema_features_distinto_del_manifiesto", "sklearn_distinto_del_manifiesto",
    "sklearn_incompatible_con_el_instalado",
}


@pytest.fixture
def entorno_ia(monkeypatch):
    """Fija el entorno IA y la instancia del servicio preventivo; los restaura al final."""
    anteriores = {k: os.environ.get(k) for k in _VARIABLES}

    def _fijar(modo: str, sha: str, raiz) -> ServicioPrediccionPreventiva:
        os.environ.update(
            IA_PREVENTIVO_MODO=modo, IA_PREVENTIVO_VERSION=VERSION, IA_PREVENTIVO_SHA256=sha,
            IA_LEGADO_MODO="experimental",
        )
        get_settings.cache_clear()
        servicio = ServicioPrediccionPreventiva(modo, VERSION, sha, raiz=raiz)
        monkeypatch.setattr(modulo_servicio, "_instancia", servicio)
        monkeypatch.setattr(rf, "_instancia", RandomForestRiesgoService())
        return servicio

    try:
        yield _fijar
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        get_settings.cache_clear()


@pytest.fixture
def registrada(tmp_path, capsys):
    """Registra VERSION con la CLI desde un CSV sintético de 5 corridas
    (prueba C4 y C5; el umbral se elige dejando una corrida fuera)."""
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=5)
    raiz = tmp_path / "registro"
    informe = tmp_path / "informe_corridas.json"
    codigo = main([
        "corridas", "--csv", str(csv), "--prueba", "C4,C5", "--horizonte", "30",
        "--registrar", "--version", VERSION, "--conjunto", "termico_minimo",
        "--directorio-registro", str(raiz), "--salida", str(informe),
    ])
    salida = capsys.readouterr()
    assert codigo == 0, salida.err
    return {"csv": csv, "raiz": raiz, "informe": informe, "resumen": json.loads(salida.out)}


def _aprobar_con_la_cli(raiz, tmp_path) -> None:
    evidencia = tmp_path / "informe_experimento_d.json"
    evidencia.write_text("{}", encoding="utf-8")
    base = ["--version", VERSION, "--responsable", "QA", "--directorio-registro", str(raiz)]
    assert main(["estado", "--nuevo", "VALIDATED_OFFLINE", "--motivo", "criterios revisados", *base]) == 0
    assert main([
        "estado", "--nuevo", ESTADO_APROBADO, "--motivo", "Experimento D aprobado", "--evidencia", str(evidencia), *base,
    ]) == 0


def test_registrar_escribe_artefacto_manifiesto_e_historial_coherentes(registrada):
    raiz, resumen = registrada["raiz"], registrada["resumen"]
    directorio = raiz / VERSION
    manifiesto = json.loads((directorio / "manifiesto.json").read_text(encoding="utf-8"))

    # SHA-256 del artefacto = manifiesto = salida de la CLI.
    assert registro.sha256_archivo(directorio / "modelo.joblib") == manifiesto["artefacto"]["sha256"] == resumen["sha256"]
    # historial.json anota el SHA-256 del manifiesto tal como quedó escrito.
    (entrada,) = json.loads((raiz / "historial.json").read_text(encoding="utf-8"))
    assert entrada["evento"] == "registrado" and entrada["version"] == VERSION
    assert entrada["sha256_artefacto"] == manifiesto["artefacto"]["sha256"]
    assert entrada["sha256_manifiesto"] == registro.sha256_archivo(directorio / "manifiesto.json")
    assert registro.sha256_manifiesto_en_historial(VERSION, raiz) == entrada["sha256_manifiesto"]

    # Lo que comprueban la carga y /activar.
    assert manifiesto["version"] == VERSION and manifiesto["estado"] == ESTADO_EXPERIMENTAL
    assert manifiesto["configuracion"]["horizonte_min"] == 30
    assert manifiesto["conjunto_features"] == "termico_minimo"
    assert manifiesto["features"] == nombres_features(ConfiguracionPreventiva(), "termico_minimo")
    assert manifiesto["versiones"]["scikit_learn"] == sklearn.__version__
    assert manifiesto["versiones"]["python"] == platform.python_version()
    assert manifiesto["versiones"]["numpy"] == np.__version__
    assert manifiesto["requisitos"]["scikit_learn_fijado"] == sklearn.__version__
    assert 0.0 < manifiesto["umbral_decision"] < 1.0
    assert manifiesto["puntuacion_calibrada"] is False

    # P-3: qué corridas ajustaron, cuáles eligieron el umbral y cuáles evaluaron.
    particiones = manifiesto["particiones"]
    assert particiones["entrenamiento"] == ["C1", "C2", "C3"] and particiones["prueba"] == ["C4", "C5"]
    assert particiones["metodo_umbral"] == "dejar_una_corrida_fuera"
    assert particiones["validacion"] == ["C1", "C2", "C3"]
    assert manifiesto["seleccion_umbral"]["umbral"] == manifiesto["umbral_decision"]
    assert "dejar una corrida fuera" in manifiesto["origen_umbral"]

    # Procedencia: huella del CSV exportado, corridas usadas y rango de fechas (sin rutas locales).
    datos = manifiesto["datos"]
    assert datos["archivo"] == "corridas.csv"
    assert datos["sha256"] == registro.sha256_archivo(registrada["csv"])
    assert datos["dataset_version"] == f"corridas-{datos['sha256'][:12]}"
    assert {c["corrida_id"]: c["rol"] for c in datos["corridas"]} == {
        "C1": "entrenamiento", "C2": "entrenamiento", "C3": "entrenamiento", "C4": "prueba", "C5": "prueba",
    }
    assert datos["desde_utc"].startswith("2026-11-02T08:00") and datos["hasta_utc"] < "2026-11-04"
    assert str(registrada["csv"].parent) not in json.dumps(manifiesto)

    # La CLI verifica lo escrito y deja el cuerpo de POST /api/ia/modelo/versiones.
    assert resumen["verificacion"] == {
        "sha256_artefacto_coincide": True, "manifiesto_anotado_en_historial": True,
        "carga_en_sombra": True, "motivo_no_disponible_en_sombra": None,
    }
    cuerpo = resumen["cuerpo_registro_api"]
    assert cuerpo["model_hash"] == manifiesto["artefacto"]["sha256"]
    assert cuerpo["feature_schema_version"] == "termico_minimo" and cuerpo["horizonte_min"] == 30
    assert cuerpo["scikit_learn_version"] == sklearn.__version__ and cuerpo["trained_at"] == manifiesto["entrenado_en"]
    assert cuerpo["particiones"]["separacion_min"] >= 30
    informe = json.loads(registrada["informe"].read_text(encoding="utf-8"))
    assert informe["registro"]["sha256"] == resumen["sha256"]
    assert "termico_minimo" in informe["resultados"]


def test_la_version_registrada_carga_en_sombra(client, token_admin, registrada, entorno_ia):
    sha = registrada["resumen"]["sha256"]
    servicio = entorno_ia(MODO_SOMBRA, sha, registrada["raiz"])

    estado = client.get("/api/ia/preventivo", headers=auth_header(token_admin))

    assert estado.status_code == 200, estado.text
    cuerpo = estado.json()
    assert (cuerpo["modo"], cuerpo["disponible"], cuerpo["motivo_codigo"]) == ("sombra", True, None)
    assert (cuerpo["version"], cuerpo["estado_modelo"], cuerpo["sha256"]) == (VERSION, ESTADO_EXPERIMENTAL, sha)
    assert cuerpo["fuente_entrenamiento"] == "coldcare_corridas"
    assert servicio.disponible


def test_aprobada_con_la_cli_activar_responde_200_sin_409_del_manifiesto(
    client, token_admin, registrada, entorno_ia, tmp_path
):
    raiz, resumen = registrada["raiz"], registrada["resumen"]
    sha = resumen["sha256"]
    _aprobar_con_la_cli(raiz, tmp_path)
    servicio = entorno_ia(MODO_ACTIVO, sha, raiz)
    cuerpo = resumen["cuerpo_registro_api"]
    # Las comprobaciones de /activar sobre el artefacto y el manifiesto, con los
    # valores que imprimió la CLI, no dejan ningún motivo.
    assert servicio.motivos_activacion(
        cuerpo["version"], cuerpo["model_hash"], horizonte_min=cuerpo["horizonte_min"],
        scikit_learn_version=cuerpo["scikit_learn_version"], feature_schema_version=cuerpo["feature_schema_version"],
    ) == []

    registrada_bd = client.post(
        "/api/ia/modelo/versiones", json={**cuerpo, "metricas": _METRICAS_QUE_CUMPLEN},
        headers=auth_header(token_admin),
    )
    assert registrada_bd.status_code == 201, registrada_bd.text
    assert registrada_bd.json()["activable"] is True

    respuesta = client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["activa"] is True
    estado = client.get("/api/ia/preventivo", headers=auth_header(token_admin)).json()
    assert (estado["disponible"], estado["estado_modelo"], estado["sha256"]) == (True, ESTADO_APROBADO, sha)
    assert estado["es_version_activa_registrada"] is True


def test_con_sus_propias_metricas_solo_bloquean_los_criterios(client, token_admin, registrada, entorno_ia, tmp_path):
    """Con las métricas del manifiesto (datos sintéticos, 2 incidentes) /activar
    responde 409 por el criterio principal, nunca por el manifiesto."""
    raiz, resumen = registrada["raiz"], registrada["resumen"]
    _aprobar_con_la_cli(raiz, tmp_path)
    entorno_ia(MODO_ACTIVO, resumen["sha256"], raiz)
    assert client.post(
        "/api/ia/modelo/versiones", json=resumen["cuerpo_registro_api"], headers=auth_header(token_admin)
    ).status_code == 201

    respuesta = client.post(f"/api/ia/modelo/versiones/{VERSION}/activar", headers=auth_header(token_admin))

    assert respuesta.status_code == 409, respuesta.text
    codigos = respuesta.json()["codigos"]
    assert codigos == ["no_cumple_criterio_principal"]
    assert not set(codigos) & _CODIGOS_DEL_MANIFIESTO


def test_con_validacion_el_manifiesto_registra_las_corridas_de_cada_particion(tmp_path, capsys):
    csv = escribir_corridas(tmp_path / "corridas.csv", corridas=6)
    raiz = tmp_path / "registro"

    codigo = main([
        "corridas", "--csv", str(csv), "--prueba", "C4,C5", "--validacion", "C3",
        "--registrar", "--version", VERSION, "--directorio-registro", str(raiz),
    ])

    assert codigo == 0, capsys.readouterr().err
    manifiesto = registro.leer_manifiesto(VERSION, raiz)
    particiones = manifiesto["particiones"]
    assert particiones["metodo_umbral"] == "particion_validacion"
    assert (particiones["entrenamiento"], particiones["validacion"], particiones["prueba"]) == (
        ["C1", "C2", "C6"], ["C3"], ["C4", "C5"],
    )
    assert manifiesto["seleccion_umbral"]["corridas_validacion"] == ["C3"]
    assert manifiesto["seleccion_umbral"]["observaciones"] == particiones["observaciones_validacion"]
    assert {c["corrida_id"]: c["rol"] for c in manifiesto["datos"]["corridas"]}["C3"] == "validacion"
    assert "corridas de validación" in manifiesto["origen_umbral"]


def test_mismo_csv_y_mismas_corridas_dan_el_mismo_artefacto_y_umbral(registrada, tmp_path, capsys):
    """09_REPRODUCIBILITY.md: con el mismo CSV, corridas y entorno, el artefacto
    sale idéntico (mismo SHA-256) y el umbral también."""
    otra_raiz = tmp_path / "registro_repeticion"

    assert main([
        "corridas", "--csv", str(registrada["csv"]), "--prueba", "C4,C5", "--horizonte", "30",
        "--registrar", "--version", VERSION, "--directorio-registro", str(otra_raiz),
    ]) == 0

    repetido = json.loads(capsys.readouterr().out)
    assert repetido["sha256"] == registrada["resumen"]["sha256"]
    assert repetido["umbral_decision"] == registrada["resumen"]["umbral_decision"]
