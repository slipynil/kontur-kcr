FROM python:3.14-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir poetry==2.5.1 \
    && python -m venv /opt/import-venv
ENV VIRTUAL_ENV=/opt/import-venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH" \
    POETRY_NO_INTERACTION=1
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

FROM builder AS test-dependencies
RUN poetry install --no-root

FROM python:3.14-slim AS app
WORKDIR /app
ENV PATH="/opt/import-venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
RUN groupadd -g 10001 appgroup \
    && useradd -u 10001 -g appgroup -m appuser \
    && mkdir -p /var/lib/import-uploads \
    && chown appuser:appgroup /var/lib/import-uploads
COPY import_worker ./import_worker
COPY alembic.ini ./
COPY migrations ./migrations
USER 10001:10001

FROM app AS test
COPY --from=test-dependencies /opt/import-venv /opt/import-venv
COPY pyproject.toml ./
COPY tests ./tests
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]

FROM app AS runtime
COPY --from=builder /opt/import-venv /opt/import-venv
CMD ["uvicorn", "import_worker.api:create_app", "--factory", "--host", "0.0.0.0"]
