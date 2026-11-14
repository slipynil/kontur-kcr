import asyncio
import logging
import os
from contextlib import asynccontextmanager
from secrets import compare_digest
from threading import Event
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError

from operator_work.assignments import Assignments, WorkError
from operator_work.cleanup import Cleanup
from operator_work.contracts import (
    AssignmentState,
    CleanupBody,
    CycleList,
    EventListResponse,
    KcrCode,
    PoolResponse,
    SnapshotDelivery,
)
from operator_work.events import CallRequest, ClientEvents, HistoricalCommentRequest
from operator_work.wait_expiry import WaitExpiry

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)
Quarter = Annotated[str, Query(pattern=r"^[0-9]{4}_Q[1-4]$")]


class TakeRequest(BaseModel):
    expected_assignment_id: UUID | None
    request_id: UUID


class PublishPoolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codes: Annotated[list[KcrCode], Field(min_length=1, max_length=1000)]


class SnapshotBalance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kcr_code: KcrCode
    outstanding_documents: Annotated[int, Field(strict=True, ge=0)]


class ApplySnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    clients: Annotated[list[SnapshotBalance], Field(min_length=1, max_length=1000)]
    delivery: SnapshotDelivery | None = None


def create_app(engine: Engine | None = None, token: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service_token = token if token is not None else os.environ["OPERATOR_WORK_SERVICE_TOKEN"]
        if not service_token:
            raise ValueError("OPERATOR_WORK_SERVICE_TOKEN must not be empty")
        database = (
            engine
            if engine is not None
            else create_engine(os.environ["OPERATOR_WORK_DATABASE_URL"], pool_pre_ping=True)
        )
        assignments = Assignments(database)
        app.state.assignments = assignments
        app.state.events = ClientEvents(database)
        app.state.cleanup = Cleanup(database)
        app.state.service_token = service_token
        stop = asyncio.Event()

        async def process_deadlines():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(assignments.settle_due)
                except SQLAlchemyError:
                    logger.exception("Could not process reassignment deadlines")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except TimeoutError:
                    pass

        async def process_waits(url: str):
            with httpx.Client(base_url=url, timeout=3, trust_env=False) as clients:
                expiry = WaitExpiry(database, clients)
                while not stop.is_set():
                    try:
                        await asyncio.to_thread(expiry.process, stop_waits.is_set)
                    except SQLAlchemyError:
                        logger.exception("Could not process expired waits")
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=30)
                    except TimeoutError:
                        pass

        stop_waits = Event()
        clients_url = os.environ.get("OPERATOR_WORK_CLIENTS_URL")
        task = asyncio.create_task(process_deadlines())
        wait_task = asyncio.create_task(process_waits(clients_url)) if clients_url else None
        try:
            yield
        finally:
            stop_waits.set()
            stop.set()
            await task
            if wait_task is not None:
                await wait_task
            if engine is None:
                database.dispose()

    app = FastAPI(title="Operator Work Service", lifespan=lifespan)

    @app.exception_handler(WorkError)
    async def work_error(_request, error: WorkError):
        return JSONResponse(
            status_code=error.status, content={"code": error.code, "message": str(error)}
        )

    def trusted_service(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if credentials is None or not compare_digest(
            credentials.credentials.encode(), app.state.service_token.encode()
        ):
            raise HTTPException(401, "Сервисная авторизация обязательна")

    def actor(
        _trusted: Annotated[None, Depends(trusted_service)],
        x_user_id: Annotated[UUID, Header()],
        x_user_role: Annotated[Literal["ADMIN", "OPERATOR"], Header()],
    ) -> tuple[UUID, str]:
        return x_user_id, x_user_role

    def operator(user: Annotated[tuple[UUID, str], Depends(actor)]) -> UUID:
        user_id, role = user
        if role != "OPERATOR":
            raise HTTPException(403, "Действие доступно оператору")
        return user_id

    def admin(user: Annotated[tuple[UUID, str], Depends(actor)]) -> UUID:
        user_id, role = user
        if role != "ADMIN":
            raise HTTPException(403, "Действие доступно администратору")
        return user_id

    @app.put("/internal/v1/clients/{kcr_code}/publication", status_code=204)
    def publish(kcr_code: str, work_quarter: Quarter, user: Annotated[UUID, Depends(admin)]):
        app.state.assignments.publish(kcr_code, work_quarter, user)
        return Response(status_code=204)

    @app.put("/internal/v1/pool/publication", status_code=204)
    def publish_pool(
        body: PublishPoolRequest, work_quarter: Quarter, user: Annotated[UUID, Depends(admin)]
    ):
        app.state.assignments.publish_batch(body.codes, work_quarter, user)
        return Response(status_code=204)

    @app.put("/internal/v1/cycles/apply-snapshot", status_code=204)
    def apply_snapshot(
        body: ApplySnapshotRequest, work_quarter: Quarter, user: Annotated[UUID, Depends(admin)]
    ):
        balances = {client.kcr_code: client.outstanding_documents for client in body.clients}
        if len(balances) != len(body.clients):
            raise WorkError(422, "DUPLICATE_KCR_CODE", "Пакет содержит повторяющиеся коды клиентов")
        app.state.assignments.apply_snapshot(
            body.batch_id, work_quarter, balances, user, body.delivery
        )
        return Response(status_code=204)

    @app.get("/internal/v1/pool", response_model=PoolResponse, operation_id="listPublishedClients")
    def pool(work_quarter: Quarter, _user: Annotated[tuple[UUID, str], Depends(actor)]):
        return {"items": app.state.assignments.pool(work_quarter)}

    @app.get(
        "/internal/v1/clients/{kcr_code}/cycles",
        response_model=CycleList,
        operation_id="listWorkCycles",
    )
    def cycles(kcr_code: str, _user: Annotated[UUID, Depends(admin)]):
        return app.state.assignments.cycles(kcr_code)

    @app.get(
        "/internal/v1/clients/{kcr_code}/assignment",
        response_model=AssignmentState,
        operation_id="readAssignment",
    )
    def assignment(
        kcr_code: str, work_quarter: Quarter, _user: Annotated[tuple[UUID, str], Depends(actor)]
    ):
        return app.state.assignments.state(kcr_code, work_quarter)

    @app.post(
        "/internal/v1/clients/{kcr_code}/take", responses={202: {"description": "Запрос создан"}}
    )
    def take(
        kcr_code: str,
        work_quarter: Quarter,
        body: TakeRequest,
        user: Annotated[UUID, Depends(operator)],
    ):
        status, result = app.state.assignments.take(
            kcr_code, work_quarter, user, body.expected_assignment_id, body.request_id
        )
        return JSONResponse(status_code=status, content=jsonable_encoder(result))

    @app.get(
        "/internal/v1/clients/{kcr_code}/events",
        response_model=EventListResponse,
        operation_id="listClientEvents",
    )
    def list_events(
        kcr_code: str,
        user: Annotated[tuple[UUID, str], Depends(actor)],
        work_quarter: Annotated[str | None, Query(pattern=r"^[0-9]{4}_Q[1-4]$")] = None,
        page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        return app.state.events.list(kcr_code, work_quarter, page, page_size, user[1])

    @app.post("/internal/v1/clients/{kcr_code}/events", status_code=201)
    def save_event(
        kcr_code: str,
        body: Annotated[CallRequest | HistoricalCommentRequest, Field(discriminator="event_type")],
        user: Annotated[UUID, Depends(operator)],
    ):
        events = app.state.events
        if isinstance(body, HistoricalCommentRequest):
            status, event = events.save_historical_comment(kcr_code, user, body)
        else:
            status, event = events.save_call(kcr_code, user, body)
        return JSONResponse(status_code=status, content=jsonable_encoder(event))

    @app.post("/internal/v1/data-cleanup")
    def cleanup_client(body: CleanupBody, user: Annotated[UUID, Depends(admin)]):
        app.state.cleanup.apply(body, user)
        return {
            "operation_id": body.operation_id,
            "kcr_code": body.kcr_code,
            "scope": body.scope,
            "status": "COMPLETED",
        }

    @app.get("/internal/v1/reassignment-notifications")
    def notifications(
        user: Annotated[UUID, Depends(operator)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return {"items": app.state.assignments.notifications(user, limit, offset)}

    @app.post("/internal/v1/reassignment-notifications/{request_id}/reject")
    def reject(request_id: UUID, user: Annotated[UUID, Depends(operator)]):
        status, result = app.state.assignments.reject(request_id, user)
        return JSONResponse(status_code=status, content=jsonable_encoder(result))

    return app
