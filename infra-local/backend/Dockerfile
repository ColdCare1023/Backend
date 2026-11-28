# BE-022: imagen base fijada por digest (python:3.12-slim, multi-arquitectura),
# no por etiqueta: la etiqueta se mueve y cada build podría usar otra imagen.
# Para actualizarla, obtener el digest nuevo del índice y revisar el cambio.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# HU-34 esc. 2 / HU-46 (criterios del 20/11/2026): el modelo preventivo opera en
# modo sombra (predice y deja evidencia rotulada, sin cambiar el riesgo ni abrir
# alertas). Las variables del servicio en Railway, si existen, prevalecen.
ENV IA_PREVENTIVO_MODO=sombra \
    IA_PREVENTIVO_VERSION=rf-preventivo-h30-termico-puerta-1.0.0 \
    IA_PREVENTIVO_SHA256=6d5c81c0403f5c96b70accc1b079f6ff6760e45e7cf83abdc12f4c7d49f6aaa6
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
# BE-022: las migraciones son un paso aparte (servicio `migraciones` en
# docker-compose; `preDeployCommand` en railway.json), no parte del arranque
# de cada réplica.
# HU-55/HU-12: el arranque vive en scripts/arrancar_api.py, el mismo que usan el
# Procfile y railpack.json cuando Railway construye con Railpack (que no usa este
# CMD; en producción tampoco se aplicó railway.json). Fija un solo worker (SSE, cola de notificaciones y
# consumidor MQTT con client_id fijo viven en el proceso, README «Topología»),
# el puerto de $PORT (8000 en local) y la confianza en el proxy:
# --proxy-headers con FORWARDED_ALLOW_IPS (por defecto solo 127.0.0.1; definirla
# con la red del proxy inverso, nunca "*" si el proxy no reemplaza la cabecera
# X-Forwarded-For que envía el cliente). /health/detalle muestra el lanzador, la
# IP y el esquema que ve el backend (docs/operacion/HU-55_despliegue_railway.md).
# Con un solo worker uvicorn sirve en el mismo proceso python (PID 1), que
# recibe el SIGTERM del redespliegue.
CMD ["python", "-m", "scripts.arrancar_api"]
