"""Endpoints de evidencia del modelo Random Forest (RF-08 / RNF-04)."""

from tests.conftest import auth_header

VECTOR_NORMAL = {
    "temperatura_ambiental": 21.0,
    "humedad_ambiental": 55.0,
    "temperatura_interna": 5.0,
    "diferencia_sensores": 16.0,
    "duracion_fuera_rango": 0.0,
    "frecuencia_desviaciones": 0.0,
    "tendencia_termica": 0.0,
    "apertura_refrigerador": False,
    "hora_evento": 12,
    "estado_conectividad_online": True,
}


def test_metadata_del_modelo_requiere_autenticacion(client):
    assert client.get("/api/ia/modelo").status_code == 401


async def test_metadata_del_modelo_reporta_metricas_rnf04(client, token_farmaceutico):
    respuesta = client.get("/api/ia/modelo", headers=auth_header(token_farmaceutico))
    assert respuesta.status_code == 200

    cuerpo = respuesta.json()
    assert cuerpo["modelo_disponible"] is True

    metricas = cuerpo["metricas"]
    # RNF-04: F1 ponderado >= 0.85 con reporte completo por clase.
    assert metricas["f1_weighted"] >= 0.85
    assert metricas["rnf04"]["cumplido"] is True
    assert "accuracy" in metricas
    assert "confusion_matrix" in metricas
    # HU-18 criterio 4: clasificador binario — excursion_critica nunca es una
    # clase del modelo, viene solo de la regla determinista de rango.
    assert set(metricas["classes"]) == {"normal", "riesgo_preventivo"}
    for clase in metricas["classes"]:
        reporte = metricas["classification_report"][clase]
        assert {"precision", "recall", "f1-score"} <= set(reporte)
    assert metricas["cross_validation"]["folds"] == 5

    assert cuerpo["metadata"]["model_version"]
    assert cuerpo["metadata"]["feature_names"]


async def test_clasificar_lectura_normal(client, token_farmaceutico):
    respuesta = client.post(
        "/api/ia/clasificar", json=VECTOR_NORMAL, headers=auth_header(token_farmaceutico)
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["nivel_riesgo"] == "normal"
    assert 0.0 <= cuerpo["confianza"] <= 1.0
    assert cuerpo["origen"] in ("random_forest", "salvaguarda_determinista")


async def test_clasificar_fuera_de_rango_prolongado_es_riesgo_preventivo_no_critica(
    client, token_farmaceutico
):
    """HU-18 criterio 4: este endpoint solo ejecuta el pipeline de IA/regla
    binaria — nunca produce excursion_critica, sin importar cuán severa sea
    la entrada. excursion_critica es exclusiva de la regla determinista de
    rango aplicada a la temperatura ACTUAL de una lectura real (HU-21), un
    concepto distinto de esta clasificación preventiva por vector de features."""
    vector = {
        **VECTOR_NORMAL,
        "temperatura_interna": 14.5,
        "duracion_fuera_rango": 90.0,
        "frecuencia_desviaciones": 5.0,
        "tendencia_termica": 2.5,
        "apertura_refrigerador": True,
    }
    respuesta = client.post(
        "/api/ia/clasificar", json=vector, headers=auth_header(token_farmaceutico)
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["nivel_riesgo"] == "riesgo_preventivo"


async def test_clasificar_denegado_para_tecnico(client, token_tecnico):
    respuesta = client.post(
        "/api/ia/clasificar", json=VECTOR_NORMAL, headers=auth_header(token_tecnico)
    )
    assert respuesta.status_code == 403
