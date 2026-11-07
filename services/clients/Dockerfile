FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home clients
COPY alembic.ini ./
COPY migrations ./migrations
USER clients
EXPOSE 8000
CMD ["uvicorn", "clients_service.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
