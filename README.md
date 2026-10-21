# ColdCare Backend

Servicio backend del sistema de monitoreo de cadena de frío (FastAPI + PostgreSQL + MQTT/TLS) y contrato de
mensajes con el nodo ColdCare1023/IoT. Es la API que consume ColdCare1023/Frontend.

| Carpeta | Contenido |
|---|---|
| `src/` | Servicio: dominio, casos de uso, infraestructura (MQTT, base de datos, IA, PDF) e interfaz (API REST, SSE) |
| `alembic/` | Migraciones de PostgreSQL |
| `tests/` | Pruebas unitarias y de integración (`pytest`) |
| `scripts/` | `seed_dev.py` (usuarios de prueba) y simulador |
| [`contrato/`](contrato/README.md) | Contrato v1.0 nodo ↔ backend (`TelemetriaV1`, `EventoV1`, acuse) y ejemplos del firmware |
| [`banco_mqtt_local/`](banco_mqtt_local/README.md) | Banco mosquitto/TLS y backend simulado para probar el firmware aislado |
| [`simulacion_excursiones/`](simulacion_excursiones/README.md) | Excursiones simuladas para la demostración |
| [`infra-local/`](infra-local/README.md) | PostgreSQL + EMQX (TLS, ACL) + backend en Docker, para probar todo en una PC |
| `docs/evidencias/` | Evidencia de QA por historia de usuario |
| [`docs/ml/`](docs/ml/) | Modelo preventivo Random Forest: auditoría de datos, metodología, resultados, integración, reproducibilidad y alcance del entrenamiento (`10_TRAINING_EXTENT_AND_BEST_SCENARIO.md`) |

## Ejecutar

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                                 # ajustar DATABASE_URL, MQTT_*, JWT_SECRET_KEY
alembic upgrade head
python -m scripts.seed_dev
uvicorn src.interface.main:app --reload
pytest
```

Todo el stack (PostgreSQL, EMQX con TLS y el backend) en local: ver [`infra-local/`](infra-local/README.md).

## Dependencias

`requirements.in` y `requirements-dev.in` declaran las dependencias directas.
`requirements.txt` y `requirements-dev.txt` son lockfiles generados: versiones
exactas y hashes SHA-256, resueltos para todas las plataformas (`--universal`).
Al contener hashes, `pip install -r requirements.txt` (Railway, Docker, CI)
instala exactamente lo probado y rechaza cualquier paquete alterado.

Tras editar un `.in`, regenerar ambos lockfiles y auditar:

```bash
uv pip compile requirements.in --universal --generate-hashes --python-version 3.12 -o requirements.txt
uv pip compile requirements-dev.in --universal --generate-hashes --python-version 3.12 -c requirements.txt -o requirements-dev.txt
pip-audit --require-hashes --disable-pip -r requirements-dev.txt
```

Para actualizar un paquete concreto: añadir `--upgrade-package <nombre>` a los
dos comandos de compilación.
