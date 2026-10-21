# BE-022: imagen base fijada por digest (python:3.13-slim, multi-arquitectura),
# no por etiqueta: la etiqueta se mueve y cada build podría usar otra imagen.
# Para actualizarla, obtener el digest nuevo del índice y revisar el cambio.
FROM python:3.13-slim@sha256:98ce6fb13b3803744fbca11434f00818f1e300e4e48a4f204e07b2c928b6218e
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
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
# docker-compose), no parte del arranque de cada réplica.
CMD ["uvicorn", "src.interface.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
