FROM python:3.14-slim
WORKDIR /app
ENV POETRY_VIRTUALENVS_CREATE=false PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir poetry==2.2.1
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main --no-interaction
COPY alembic.ini ./
COPY alembic ./alembic
COPY operator_work ./operator_work
# Listen port, overridable at runtime; EXPOSE documents the default.
ENV OPERATOR_WORK_PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn operator_work.api:create_app --factory --host 0.0.0.0 --port ${OPERATOR_WORK_PORT}"]
