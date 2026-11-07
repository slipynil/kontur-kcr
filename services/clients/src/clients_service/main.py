from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from clients_service.config import Settings
from clients_service.database import SCHEMA_REVISION, build_engine
from clients_service.import_application import PayloadConflict, apply_import
from clients_service.import_models import ImportEnvelope
from clients_service.read_api import router as read_router
from clients_service.read_repository import ClientNotFound


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = build_engine(configuration)
        app.state.engine = engine
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Clients Service", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        return JSONResponse(
            {"code": "INVALID_REQUEST", "message": "Invalid request"}, status_code=400
        )

    @app.exception_handler(ClientNotFound)
    async def client_not_found(request: Request, error: ClientNotFound):
        return JSONResponse(
            {"code": "CLIENT_NOT_FOUND", "message": "Client not found"}, status_code=404
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(request: Request, error: SQLAlchemyError):
        return JSONResponse(
            {"code": "SERVICE_UNAVAILABLE", "message": "Try again later"}, status_code=503
        )

    if configuration.enable_registry_read:
        app.include_router(read_router)

    if configuration.enable_import_apply:

        @app.post("/internal/v1/import/apply", tags=["Import"], response_model=None)
        def import_apply(envelope: ImportEnvelope, request: Request) -> JSONResponse:
            try:
                result = apply_import(request.app.state.engine, envelope)
            except PayloadConflict:
                return JSONResponse(
                    {
                        "code": "IMPORT_PAYLOAD_CONFLICT",
                        "message": "Batch ID has different content",
                    },
                    status_code=409,
                )
            except ValueError:
                return JSONResponse(
                    {"code": "INVALID_REQUEST", "message": "Invalid request"}, status_code=400
                )
            except SQLAlchemyError:
                return JSONResponse(
                    {
                        "code": "IMPORT_RETRY_REQUIRED",
                        "message": "Retry the same batch later",
                    },
                    status_code=503,
                )
            return JSONResponse(result)

    @app.get("/health/live", tags=["Health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["Health"], response_model=None)
    def ready(request: Request) -> JSONResponse:
        try:
            with request.app.state.engine.connect() as connection:
                revisions = (
                    connection.execute(text("SELECT version_num FROM alembic_version"))
                    .scalars()
                    .all()
                )
            if revisions != [SCHEMA_REVISION]:
                return JSONResponse({"status": "not_ready"}, status_code=503)
        except SQLAlchemyError:
            # Do not expose connection strings, credentials or database diagnostics.
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return app
