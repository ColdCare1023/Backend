"""B13 — cuotas por endpoint.

El límite global (240 req/min por IP) trata igual a un GET barato que a una
verificación de cadena O(n). Estas pruebas fijan el comportamiento de las
cuotas específicas: que existan, que devuelvan 429 con `Retry-After`, que no se
contagien entre endpoints ni entre usuarios, y —sobre todo— que no estrangulen
la ingesta legítima de lecturas.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import auth_header


def _lectura(device_id: str = "esp32-rate-limit", desplazamiento_segundos: int = 0) -> dict:
    timestamp = datetime.now(timezone.utc) - timedelta(seconds=desplazamiento_segundos)
    return {
        "device_id": device_id,
        "timestamp": timestamp.isoformat(),
        "temperatura_ambiental": 21.0,
        "humedad_ambiental": 55.0,
        "temperatura_interna": 5.0,
        "apertura_refrigerador": False,
        "estado_conectividad": "online",
    }


class TestVerificacionCadena:
    """GET /api/trazabilidad/verificar — 5 por minuto y por usuario."""

    @pytest.mark.asyncio
    async def test_permite_las_primeras_cinco_verificaciones(self, client, token_farmaceutico):
        for _ in range(5):
            respuesta = client.get("/api/trazabilidad/verificar", headers=auth_header(token_farmaceutico))
            assert respuesta.status_code == 200

    @pytest.mark.asyncio
    async def test_la_sexta_verificacion_recibe_429(self, client, token_farmaceutico):
        for _ in range(5):
            client.get("/api/trazabilidad/verificar", headers=auth_header(token_farmaceutico))

        respuesta = client.get("/api/trazabilidad/verificar", headers=auth_header(token_farmaceutico))

        assert respuesta.status_code == 429

    @pytest.mark.asyncio
    async def test_el_429_indica_cuando_reintentar(self, client, token_farmaceutico):
        for _ in range(6):
            respuesta = client.get(
                "/api/trazabilidad/verificar", headers=auth_header(token_farmaceutico)
            )

        # Sin `Retry-After` el cliente solo puede reintentar a ciegas y
        # realimentar la saturación que el límite pretende evitar.
        assert respuesta.status_code == 429
        assert int(respuesta.headers["Retry-After"]) > 0

    @pytest.mark.asyncio
    async def test_la_cuota_es_por_usuario_no_por_ip(
        self, client, token_farmaceutico, token_tecnico
    ):
        # En una farmacia todos los puestos salen por la misma IP: con clave por
        # IP, el primero en verificar dejaría sin cuota a los demás.
        for _ in range(6):
            client.get("/api/trazabilidad/verificar", headers=auth_header(token_farmaceutico))

        respuesta = client.get("/api/trazabilidad/verificar", headers=auth_header(token_tecnico))

        assert respuesta.status_code == 200

    @pytest.mark.asyncio
    async def test_agotar_la_verificacion_no_bloquea_otras_rutas(self, client, token_farmaceutico):
        for _ in range(6):
            client.get("/api/trazabilidad/verificar", headers=auth_header(token_farmaceutico))

        assert client.get("/api/trazabilidad/estado", headers=auth_header(token_farmaceutico)).status_code == 200
        assert client.get("/api/trazabilidad", headers=auth_header(token_farmaceutico)).status_code == 200

    @pytest.mark.asyncio
    async def test_el_limite_no_sustituye_al_control_de_acceso(self, client):
        # La cuota no debe convertirse en la primera línea: sin token la
        # respuesta sigue siendo 401, no 429.
        respuesta = client.get("/api/trazabilidad/verificar")

        assert respuesta.status_code == 401


class TestIngestaLecturas:
    """POST /api/lecturas — cuota por IP, configurable por entorno."""

    @pytest.mark.asyncio
    async def test_la_ingesta_normal_no_se_ve_afectada(self, client, token_tecnico):
        # 20 lecturas seguidas es un ritmo muy por encima del real y debe pasar
        # entera: la cuota está para el abuso, no para el uso.
        for i in range(20):
            respuesta = client.post(
                "/api/lecturas", json=_lectura(desplazamiento_segundos=i), headers=auth_header(token_tecnico)
            )
            assert respuesta.status_code == 201

    @pytest.mark.asyncio
    async def test_supera_el_techo_configurado_y_devuelve_429(self, app, client, token_tecnico):
        # El techo se baja por override de settings en vez de enviar 121
        # lecturas: la prueba mide el mecanismo, no la paciencia del CI.
        from src.infrastructure.config import get_settings
        from src.interface.api import deps

        base = get_settings()
        recortado = base.model_copy(update={"ingesta_rate_limit_max_solicitudes": 3})
        app.dependency_overrides[deps.get_settings] = lambda: recortado

        try:
            for i in range(3):
                assert (
                    client.post(
                        "/api/lecturas",
                        json=_lectura(desplazamiento_segundos=i),
                        headers=auth_header(token_tecnico),
                    ).status_code
                    == 201
                )

            respuesta = client.post(
                "/api/lecturas", json=_lectura(desplazamiento_segundos=99), headers=auth_header(token_tecnico)
            )

            assert respuesta.status_code == 429
            assert int(respuesta.headers["Retry-After"]) > 0
        finally:
            app.dependency_overrides.pop(deps.get_settings, None)

    @pytest.mark.asyncio
    async def test_la_lectura_rechazada_por_cuota_no_se_persiste(self, app, client, token_tecnico):
        from src.infrastructure.config import get_settings
        from src.interface.api import deps

        base = get_settings()
        recortado = base.model_copy(update={"ingesta_rate_limit_max_solicitudes": 2})
        app.dependency_overrides[deps.get_settings] = lambda: recortado

        try:
            for i in range(2):
                client.post(
                    "/api/lecturas",
                    json=_lectura("esp32-cuota", desplazamiento_segundos=i),
                    headers=auth_header(token_tecnico),
                )
            client.post(
                "/api/lecturas",
                json=_lectura("esp32-cuota", desplazamiento_segundos=50),
                headers=auth_header(token_tecnico),
            )
        finally:
            app.dependency_overrides.pop(deps.get_settings, None)

        historial = client.get(
            "/api/lecturas", params={"device_id": "esp32-cuota"}, headers=auth_header(token_tecnico)
        )
        assert historial.status_code == 200
        # La cuota corta antes del caso de uso: nada a medio escribir, y la
        # cadena de trazabilidad no gana un eslabón por una petición rechazada.
        assert len(historial.json()) == 2

    @pytest.mark.asyncio
    async def test_agotar_la_ingesta_no_bloquea_la_consulta_de_historial(
        self, app, client, token_tecnico
    ):
        from src.infrastructure.config import get_settings
        from src.interface.api import deps

        base = get_settings()
        recortado = base.model_copy(update={"ingesta_rate_limit_max_solicitudes": 1})
        app.dependency_overrides[deps.get_settings] = lambda: recortado

        try:
            client.post("/api/lecturas", json=_lectura(), headers=auth_header(token_tecnico))
            bloqueada = client.post(
                "/api/lecturas", json=_lectura(desplazamiento_segundos=10), headers=auth_header(token_tecnico)
            )
            assert bloqueada.status_code == 429
        finally:
            app.dependency_overrides.pop(deps.get_settings, None)

        # Que la ingesta esté saturada no puede dejar al farmacéutico sin ver la
        # evidencia térmica ya registrada.
        assert client.get("/api/lecturas", headers=auth_header(token_tecnico)).status_code == 200


class TestAislamientoEntreInstancias:
    @pytest.mark.asyncio
    async def test_cada_aplicacion_arranca_con_la_cuota_limpia(self, client, token_farmaceutico):
        # Los limitadores viven en `app.state`, no a nivel de módulo: si fueran
        # globales, esta prueba heredaría la cuota agotada por las anteriores y
        # el fallo aparecería de forma intermitente según el orden.
        respuesta = client.get("/api/trazabilidad/verificar", headers=auth_header(token_farmaceutico))

        assert respuesta.status_code == 200
