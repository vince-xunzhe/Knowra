FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # model_gateway sits at /app/model_gateway (sibling to backend) so
    # uvicorn started from /app/backend needs /app on the path to
    # resolve `from model_gateway import ...`. Without this the cloud
    # boot dies with ModuleNotFoundError before serving any request.
    PYTHONPATH=/app

WORKDIR /app/backend

COPY backend/requirements.txt .
COPY backend/requirements-postgres.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m pip install -r requirements-postgres.txt
# requirements-postgres.txt holds psycopg2-binary, split out so macOS
# desktop installs (which lack pg_config) don't have to build it from
# source. Linux wheels for psycopg2-binary always exist, so adding it
# to the cloud image build is free.

COPY backend/ .
COPY model_gateway /app/model_gateway

EXPOSE 8000

# No --reload in production: it watches the filesystem and double-runs
# the app, eating CPU + RAM and breaking single-process state (e.g.
# the rate-limit window dict in cloud_ask).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
