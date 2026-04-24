FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOME=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos "" appuser

COPY requirements.txt /app/requirements.txt

RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

COPY alembic /app/alembic
COPY app /app/app
COPY scripts /app/scripts
COPY alembic.ini /app/alembic.ini

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "scripts/container_entrypoint.py", "api"]
