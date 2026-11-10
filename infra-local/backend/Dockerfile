# BE-022: imagen base fijada por digest (python:3.12-slim, multi-arquitectura),
# no por etiqueta: la etiqueta se mueve y cada build podría usar otra imagen.
# Para actualizarla, obtener el digest nuevo del índice y revisar el cambio.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# HU-29: pg_dump/pg_restore para el respaldo programado (scripts.respaldo_postgres)
# y la prueba de restauración. Cliente 17 (Debian trixie): compatible con
# servidores PostgreSQL ≤ 17.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
# BE-021: requirements.txt es el lockfile (versiones exactas + hashes).
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
# BE-022: el proceso no corre como root. El código queda propiedad de root y
# de solo lectura para el usuario de la aplicación, que no escribe en disco.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY . .
USER app
EXPOSE 8000
# BE-008: las cuotas por IP usan la IP real del cliente solo si el proxy es de
# confianza. Uvicorn lee FORWARDED_ALLOW_IPS (por defecto solo 127.0.0.1):
# definirla con la red del proxy inverso, nunca "*" si el proxy no reemplaza
# la cabecera X-Forwarded-For que envía el cliente.
# BE-022: las migraciones son un paso aparte (servicio `migraciones` en
# docker-compose; `preDeployCommand` en railway.json), no parte del arranque
# de cada réplica.
# HU-56: Railway asigna el puerto en $PORT (8000 en local). `exec` deja a
# uvicorn como PID 1 para que reciba el SIGTERM del redespliegue, y un solo
# worker por diseño: SSE, cola de notificaciones y consumidor MQTT con
# client_id fijo viven en el proceso (README, «Topología»).
# B-11: --forwarded-allow-ips explícito (FORWARDED_ALLOW_IPS, por defecto solo
# 127.0.0.1). /health/detalle muestra la IP y el esquema que ve el backend para
# comprobarlo en Railway (docs/operacion/HU-55_despliegue_railway.md).
CMD ["sh", "-c", "exec uvicorn src.interface.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
