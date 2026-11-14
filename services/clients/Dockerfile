FROM python:3.14-slim AS build
ENV POETRY_VIRTUALENVS_IN_PROJECT=true POETRY_NO_INTERACTION=1
WORKDIR /app
RUN python -m pip install --no-cache-dir 'poetry==2.5.1'
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root
COPY src ./src
RUN poetry install --only-root

FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"
# Listen port, overridable at runtime; EXPOSE documents the default.
ENV CLIENTS_PORT=8000
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY --from=build /app/src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --uid 10001 --create-home clients
USER clients
EXPOSE 8000
CMD ["sh", "-c", "uvicorn clients_service.main:create_app --factory --host 0.0.0.0 --port ${CLIENTS_PORT} --no-access-log"]
