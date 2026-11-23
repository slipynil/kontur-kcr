import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from client_history.config import Settings
from client_history.errors import HistoryError, unavailable
from client_history.models import (
    HistoryCleanupRequest,
    HistoryCleanupResult,
    HistoryEventBatch,
    HistoryEventListResponse,
    IngestResult,
    KcrCode,
    Quarter,
)
from client_history.service import History
from client_history.upstreams import Names, Registry

logger = logging.getLogger(__name__)
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_schema_head: str | None = None


def schema_head() -> str:
    """The Alembic revision the ready database is expected to carry."""
    global _schema_head
    if _schema_head is None:
        _schema_head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
    return _schema_head


def error_response(error: HistoryError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status, content={"code": error.code, "message": str(error)}
    )


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    gateway: httpx.Client | None = None,
    registry: httpx.Client | None = None,
) -> FastAPI:
    config = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned_engine = (
            engine
            if engine is not None
            else create_engine(config.database_url, pool_pre_ping=True, hide_parameters=True)
        )
        owned_clients: list[httpx.Client] = []
        gateway_client, registry_client = gateway, registry
        if gateway_client is None and config.gateway_url:
            gateway_client = httpx.Client(
                base_url=config.gateway_url, timeout=config.upstream_timeout, trust_env=False
            )
            owned_clients.append(gateway_client)
        if registry_client is None and config.clients_url:
            registry_client = httpx.Client(
                base_url=config.clients_url, timeout=config.upstream_timeout, trust_env=False
            )
            owned_clients.append(registry_client)
        app.state.engine = owned_engine
        app.state.history = History(owned_engine, Names(gateway_client), Registry(registry_client))
        try:
            yield
        finally:
            for client in owned_clients:
                client.close()
            if engine is None:
                owned_engine.dispose()

    app = FastAPI(title="Client History API", lifespan=lifespan)

    # Внутренний контур изолируется на уровне сети; проверку личности и роли
    # выполняет Gateway (ADR-0010), видимость клиента для оператора — тоже он (ADR-0014).
    @app.exception_handler(HistoryError)
    async def handle_history_error(_request: Request, error: HistoryError) -> JSONResponse:
        return error_response(error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return error_response(HistoryError(422, "VALIDATION_ERROR", "Некорректные данные запроса"))

    @app.exception_handler(SQLAlchemyError)
    async def handle_database(_request: Request, error: SQLAlchemyError) -> JSONResponse:
        logger.warning("Хронику не удалось прочитать или сохранить (%s)", type(error).__name__)
        return error_response(unavailable())

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(_request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
            error.status_code, f"HTTP_{error.status_code}"
        )
        return JSONResponse(
            status_code=error.status_code,
            content={"code": code, "message": str(error.detail)},
        )

    @app.post(
        "/internal/v1/history/events",
        operation_id="ingestHistoryEvents",
        response_model=IngestResult,
    )
    def ingest(body: HistoryEventBatch) -> IngestResult:
        return app.state.history.ingest(body)

    @app.post(
        "/internal/v1/history/cleanup",
        operation_id="cleanupHistory",
        response_model=HistoryCleanupResult,
    )
    def cleanup(body: HistoryCleanupRequest) -> HistoryCleanupResult:
        return app.state.history.cleanup(body)

    @app.get(
        "/internal/v1/clients/{kcr_code}/events",
        operation_id="listHistoryEvents",
        response_model=HistoryEventListResponse,
    )
    def list_events(
        kcr_code: KcrCode,
        work_quarter: Annotated[Quarter | None, Query()] = None,
        page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> HistoryEventListResponse:
        return app.state.history.list(kcr_code, work_quarter, page, page_size)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        try:
            with app.state.engine.connect() as connection:
                revisions = set(
                    connection.scalars(text("SELECT version_num FROM alembic_version")).all()
                )
        except SQLAlchemyError:
            revisions = set()
        healthy = revisions == {schema_head()}
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "not_ready"},
        )

    return app
