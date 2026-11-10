FROM python:3.14-slim
WORKDIR /app
ENV POETRY_VIRTUALENVS_CREATE=false PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir poetry==2.2.1
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main --no-interaction
COPY alembic.ini ./
COPY alembic ./alembic
COPY operator_work ./operator_work
CMD ["uvicorn", "operator_work.api:create_app", "--factory", "--host", "0.0.0.0"]
