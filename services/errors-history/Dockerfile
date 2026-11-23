FROM python:3.14-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir poetry==2.5.1 \
    && python -m venv /opt/errors-history-venv
ENV VIRTUAL_ENV=/opt/errors-history-venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH" \
    POETRY_NO_INTERACTION=1
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

FROM builder AS test-dependencies
RUN poetry install --no-root

FROM python:3.14-slim AS app
WORKDIR /app
ENV PATH="/opt/errors-history-venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
RUN groupadd -g 10001 appgroup \
    && useradd -u 10001 -g appgroup -m appuser
COPY errors_history ./errors_history
COPY alembic.ini ./
COPY migrations ./migrations
USER 10001:10001

FROM app AS test
ENV COVERAGE_FILE=/tmp/.coverage
COPY --from=test-dependencies /opt/errors-history-venv /opt/errors-history-venv
COPY pyproject.toml ./
COPY tests ./tests
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]

FROM app AS runtime
COPY --from=builder /opt/errors-history-venv /opt/errors-history-venv
ENV ERRORS_HISTORY_PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn errors_history.api:create_app --factory --host 0.0.0.0 --port \"${ERRORS_HISTORY_PORT}\" --no-access-log"]
