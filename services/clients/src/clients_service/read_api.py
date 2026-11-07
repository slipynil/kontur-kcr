from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from clients_service.import_models import Quarter, Tag
from clients_service.read_models import ClientCard, ClientPage, Duration, Dynamics, HistoryPage
from clients_service.read_repository import get_card, get_history, list_clients

router = APIRouter(prefix="/internal/v1/clients", tags=["Registry"])


def validate_unique_parameters(request: Request) -> None:
    if any(len(request.query_params.getlist(key)) > 1 for key in request.query_params):
        raise RequestValidationError(
            [
                {"loc": ("query",), "type": "value_error", "msg": "Repeated query parameters"},
            ]
        )


class PeriodQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quarter: Quarter | None = None

    @field_validator("quarter")
    @classmethod
    def validate_year(cls, value):
        if value is not None and int(value[:4]) == 0:
            raise ValueError("Calendar quarters require a positive year")
        return value


class ListQuery(PeriodQuery):
    search: Annotated[str, Field(max_length=256)] | None = None
    dynamics: Dynamics | None = None
    duration: Duration | None = None
    tags: str | None = None
    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=20, ge=1, le=100)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: str | None):
        if value is None or not value.strip():
            return None
        return ",".join(sorted({Tag(part.strip()).value for part in value.split(",")}))


class CardQuery(PeriodQuery):
    snapshots_limit: int = Field(default=8, ge=1, le=50)


class HistoryQuery(PeriodQuery):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100_000_000)


@router.get("", response_model=ClientPage, operation_id="listClients")
def registry(request: Request, query: Annotated[ListQuery, Query()]):
    validate_unique_parameters(request)
    parameters = query.model_dump()
    parameters["tags"] = query.tags.split(",") if query.tags else ()
    if parameters["search"] is not None:
        parameters["search"] = parameters["search"].strip()
    return list_clients(request.app.state.engine, **parameters)


@router.get("/{kcr_code}", response_model=ClientCard, operation_id="getClient")
def card(kcr_code: str, request: Request, query: Annotated[CardQuery, Query()]):
    validate_unique_parameters(request)
    return get_card(request.app.state.engine, kcr_code, **query.model_dump())


@router.get(
    "/{kcr_code}/snapshots/history", response_model=HistoryPage, operation_id="getSnapshotHistory"
)
def history(kcr_code: str, request: Request, query: Annotated[HistoryQuery, Query()]):
    validate_unique_parameters(request)
    return get_history(request.app.state.engine, kcr_code, **query.model_dump())
