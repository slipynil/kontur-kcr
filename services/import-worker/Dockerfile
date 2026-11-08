FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM python:3.13-slim AS base
COPY --from=uv /uv /bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev
COPY import_worker ./import_worker
COPY alembic.ini ./
COPY migrations ./migrations
RUN mkdir -p /var/lib/import-uploads && chown 10001:10001 /var/lib/import-uploads

FROM base AS test
RUN uv sync --locked
COPY tests ./tests
USER 10001:10001
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]

FROM base AS runtime
USER 10001:10001
CMD ["uvicorn", "import_worker.api:create_app", "--factory", "--host", "0.0.0.0"]
