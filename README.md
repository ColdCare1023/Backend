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
| `docs/evidencias/` | Evidencia de QA por historia de usuario |

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
