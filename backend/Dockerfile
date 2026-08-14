FROM python:3.13-slim AS runtime

ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG PYPI_TIMEOUT=120
ARG PYPI_RETRIES=3

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.8.3 /uv /uvx /bin/
COPY pyproject.toml uv.lock* ./
RUN uv export --frozen --no-dev --no-emit-project --no-header \
      --output-file /tmp/requirements.txt >/dev/null \
    && python -m venv .venv \
    && .venv/bin/python -m pip install \
      --disable-pip-version-check \
      --no-cache-dir \
      --require-hashes \
      --index-url "${PYPI_INDEX_URL}" \
      --timeout "${PYPI_TIMEOUT}" \
      --retries "${PYPI_RETRIES}" \
      --requirement /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
