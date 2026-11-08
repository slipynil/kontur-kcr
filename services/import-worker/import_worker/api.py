import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import date
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from import_worker.broker import publish_pending
from import_worker.config import Settings
from import_worker.database import ImportBatch, has_newer_clients_apply, insert_batch, report
from import_worker.error_report import XLSX_MEDIA_TYPE, errors_xlsx
from import_worker.models import QUARTER_PATTERN, Batch, ImportParameters, ImportType, Status
from import_worker.storage import InvalidUpload, store_upload, upload_path


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def error_response(error: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status, content={"code": error.code, "message": str(error)}
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    engine = create_async_engine(config.database_url, pool_pre_ping=True, hide_parameters=True)
    sessions = async_sessionmaker(engine)
    reports = async_sessionmaker(engine.execution_options(isolation_level="REPEATABLE READ"))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        publisher = asyncio.create_task(publish_pending(config.amqp_url, sessions))
        try:
            yield
        finally:
            publisher.cancel()
            with suppress(asyncio.CancelledError):
                await publisher
            await engine.dispose()

    app = FastAPI(title="Import API", lifespan=lifespan)

    @app.exception_handler(APIError)
    async def handle_api_error(_request: Request, error: APIError) -> JSONResponse:
        return error_response(error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return error_response(
            APIError(400, "INVALID_IMPORT_REQUEST", "Проверьте параметры запроса")
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_database(_request: Request, _error: SQLAlchemyError) -> JSONResponse:
        return error_response(APIError(503, "IMPORT_UNAVAILABLE", "Import DB временно недоступна"))

    @app.post("/internal/v1/imports", status_code=202, operation_id="uploadImport")
    async def upload(
        file: Annotated[UploadFile, File()],
        user_id: Annotated[UUID, Header(alias="X-User-Id")],
        selected_quarter: Annotated[str, Form(pattern=QUARTER_PATTERN)],
        import_type: Annotated[ImportType, Form()],
        snapshot_date: Annotated[str, Form(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    ) -> Batch:
        filename = PurePosixPath((file.filename or "").replace("\\", "/")).name
        if "\x00" in filename or not filename.lower().endswith(".xlsx"):
            raise APIError(400, "INVALID_IMPORT_FILE", "Требуется файл .xlsx")
        try:
            params = ImportParameters(
                selected_quarter=selected_quarter,
                import_type=import_type,
                snapshot_date=date.fromisoformat(snapshot_date),
            )
        except ValueError as exc:
            raise APIError(400, "INVALID_IMPORT_REQUEST", "Проверьте параметры запроса") from exc

        batch_id = uuid4()
        path = upload_path(config.upload_dir, batch_id)
        try:
            digest = await asyncio.to_thread(
                store_upload,
                file.file,
                path,
                config.max_upload_bytes,
            )
        except InvalidUpload as exc:
            raise APIError(400, "INVALID_IMPORT_FILE", str(exc)) from exc
        except OSError as exc:
            raise APIError(503, "IMPORT_UNAVAILABLE", "Хранилище файлов недоступно") from exc

        async with sessions.begin() as db:
            try:
                batch = await insert_batch(db, batch_id, user_id, filename, digest, params)
            except (SQLAlchemyError, OSError) as exc:
                # No commit has been attempted, so no worker can reference the file.
                with suppress(OSError):
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                raise APIError(503, "IMPORT_UNAVAILABLE", "Import DB временно недоступна") from exc
        return batch

    async def batch_report(batch_id: UUID) -> Batch:
        async with reports.begin() as db:
            batch = await db.get(ImportBatch, batch_id)
            if batch is None:
                raise APIError(404, "IMPORT_NOT_FOUND", "Пакет не найден")
            return await report(db, batch)

    @app.get("/internal/v1/imports/{batch_id}", operation_id="getImport")
    async def get_import(batch_id: UUID) -> Batch:
        return await batch_report(batch_id)

    @app.get(
        "/internal/v1/imports/{batch_id}/errors.xlsx",
        operation_id="downloadImportErrors",
        response_class=Response,
        responses={
            200: {"content": {XLSX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}}}
        },
    )
    async def download_errors(batch_id: UUID) -> Response:
        batch = await batch_report(batch_id)
        content = await asyncio.to_thread(errors_xlsx, batch.error_log)
        return Response(
            content=content,
            media_type=XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="import-errors-{batch_id}.xlsx"',
                "Cache-Control": "no-store",
            },
        )

    @app.post("/internal/v1/imports/{batch_id}/rollback", operation_id="rollbackImport")
    async def rollback(batch_id: UUID) -> Batch:
        async with reports.begin() as db:
            batch = await db.get(ImportBatch, batch_id)
            if batch is None:
                raise APIError(404, "IMPORT_NOT_FOUND", "Пакет не найден")
            if (
                batch.status in {Status.ROLLED_BACK, Status.FAILED}
                or (batch.clients_applied_at is None and batch.apply_started_at is None)
                or await has_newer_clients_apply(db, batch)
            ):
                raise APIError(
                    409, "IMPORT_NOT_LATEST", "Можно отменить только последний применённый пакет"
                )
        raise APIError(
            503, "IMPORT_ROLLBACK_UNAVAILABLE", "Координация отката между сервисами недоступна"
        )

    return app
