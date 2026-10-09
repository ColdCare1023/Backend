# Imagen única del backend: la usan Railway (railway.json) y el entorno local
# (infra-local/docker-compose.yml). HU-56.
FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# - Las migraciones van ANTES de uvicorn y con `&&`: si una falla, la API no
#   arranca sobre un esquema a medias.
# - `exec` deja a uvicorn como PID 1 para que reciba el SIGTERM de Railway y
#   cierre ordenadamente (lifespan: MQTT y cola de notificaciones).
# - 1 worker por diseño: SSE, revocación de JWT, rate limit y la cola de
#   notificaciones viven en memoria del proceso (ver README, «Topología»).
# - --proxy-headers: detrás del proxy de Railway, request.client.host debe ser
#   la IP del cliente (X-Forwarded-For), no la del proxy; de ella dependen el
#   rate limit de login y `ip_origen` en la auditoría.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn src.interface.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
