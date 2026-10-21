FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# BE-021: requirements.txt es el lockfile (versiones exactas + hashes).
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY . .
EXPOSE 8000
# BE-008: las cuotas por IP usan la IP real del cliente solo si el proxy es de
# confianza. Uvicorn lee FORWARDED_ALLOW_IPS (por defecto solo 127.0.0.1):
# definirla con la red del proxy inverso, nunca "*" si el proxy no reemplaza
# la cabecera X-Forwarded-For que envía el cliente.
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.interface.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
