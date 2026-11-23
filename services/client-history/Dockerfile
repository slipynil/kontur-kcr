FROM python:3.14-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir poetry==2.5.1 \
    && python -m venv /opt/client-history-venv
ENV VIRTUAL_ENV=/opt/client-history-venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH" \
    POETRY_NO_INTERACTION=1
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

FROM builder AS test-dependencies
RUN poetry install --no-root

FROM python:3.14-slim AS app
WORKDIR /app
ENV PATH="/opt/client-history-venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
RUN groupadd -g 10001 appgroup \
    && useradd -u 10001 -g appgroup -m appuser
COPY client_history ./client_history
COPY alembic.ini ./
COPY migrations ./migrations
USER 10001:10001

FROM app AS test
COPY --from=test-dependencies /opt/client-history-venv /opt/client-history-venv
COPY pyproject.toml ./
COPY tests ./tests
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]

FROM app AS runtime
COPY --from=builder /opt/client-history-venv /opt/client-history-venv
# Listen port of the API container, overridable at runtime.
ENV CLIENT_HISTORY_PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn client_history.api:create_app --factory --host 0.0.0.0 --port ${CLIENT_HISTORY_PORT} --no-access-log"]
