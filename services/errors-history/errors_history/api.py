import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Annotated
from uuid import UUID

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from errors_history.database import SourceErrorConflict, events, list_errors, save_errors
from errors_history.models import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE,
    MAX_PAGE_SIZE,
    ErrorBulk,
    ErrorBulkResult,
    ErrorPage,
    ImportType,
)

Page = Annotated[int, Query(ge=1, le=MAX_PAGE)]
PageSize = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


def create_app() -> FastAPI:
    engine = create_engine(
        os.environ["ERRORS_HISTORY_DATABASE_URL"],
        connect_args={"connect_timeout": 5},
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
        hide_parameters=True,
    )
    config = Config(str(FilePath(__file__).resolve().parents[1] / "alembic.ini"))
    expected_head = ScriptDirectory.from_config(config).get_current_head()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Kontur KCR Errors History", version="1.0.0", lifespan=lifespan)

    @app.exception_handler(OperationalError)
    def database_unavailable(_request: Request, _error: OperationalError) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"detail": {"code": "ERRORS_HISTORY_UNAVAILABLE"}}
        )

    @app.post(
        "/internal/v1/import-errors/bulk",
        responses={
            409: {"description": "The source error ID already has different data"},
            503: {"description": "Errors History DB is unavailable"},
        },
    )
    def bulk_import_errors(payload: ErrorBulk) -> ErrorBulkResult:
        try:
            return save_errors(engine, payload.errors)
        except SourceErrorConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IMPORT_ERROR_CONFLICT",
                    "source_error_id": error.source_error_id,
                },
            ) from error

    @app.get(
        "/internal/v1/clients/{kcr_code}/import-errors",
        responses={503: {"description": "Errors History DB is unavailable"}},
    )
    def client_import_errors(
        kcr_code: Annotated[str, Path(min_length=1, pattern=r"^[^\x00]+$")],
        import_type: ImportType | None = None,
        page: Page = 1,
        page_size: PageSize = DEFAULT_PAGE_SIZE,
    ) -> ErrorPage:
        conditions = [events.c.kcr_code == kcr_code]
        if import_type is not None:
            conditions.append(events.c.import_type == import_type)
        return list_errors(engine, conditions, page, page_size)

    @app.get(
        "/internal/v1/imports/{batch_id}/errors",
        responses={503: {"description": "Errors History DB is unavailable"}},
    )
    def batch_import_errors(
        batch_id: UUID, page: Page = 1, page_size: PageSize = DEFAULT_PAGE_SIZE
    ) -> ErrorPage:
        return list_errors(engine, [events.c.batch_id == batch_id], page, page_size)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", responses={503: {"description": "Database is not ready"}})
    def ready() -> JSONResponse:
        try:
            with engine.connect() as connection:
                heads = MigrationContext.configure(connection).get_current_heads()
        except SQLAlchemyError:
            heads = ()
        healthy = heads == (expected_head,)
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "not_ready"},
        )

    return app
