FROM python:3.14-slim AS base
WORKDIR /app
ENV POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
RUN python -m venv /opt/poetry \
    && /opt/poetry/bin/pip install --no-cache-dir poetry==2.5.1
COPY pyproject.toml poetry.lock ./
RUN /opt/poetry/bin/poetry sync --only main --no-root
COPY import_worker ./import_worker
COPY alembic.ini ./
COPY migrations ./migrations
RUN mkdir -p /var/lib/import-uploads && chown 10001:10001 /var/lib/import-uploads

FROM base AS test
RUN /opt/poetry/bin/poetry sync --no-root
COPY tests ./tests
USER 10001:10001
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]

FROM base AS runtime
USER 10001:10001
CMD ["uvicorn", "import_worker.api:create_app", "--factory", "--host", "0.0.0.0"]
