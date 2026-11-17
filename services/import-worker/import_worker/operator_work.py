from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, Field

from import_worker.database import OperatorDelivery

PART_SIZE = 1000
MAX_PARTS = 1000


class AffectedClient(BaseModel):
    kcr_code: str
    outstanding_documents: int | None = Field(ge=0)
    source_quarters: list[str] = Field(default_factory=list)


class AffectedClientsPage(BaseModel):
    batch_id: UUID
    items: list[AffectedClient]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=PART_SIZE)
    offset: int = Field(ge=0)


async def affected_clients(client: httpx.AsyncClient, batch_id: UUID) -> list[AffectedClient]:
    affected = []
    seen: set[str] = set()
    offset = 0
    total = None
    while total is None or offset < total:
        response = await client.get(
            f"/internal/v1/import/{batch_id}/affected-clients",
            params={"limit": PART_SIZE, "offset": offset},
        )
        response.raise_for_status()
        page = AffectedClientsPage.model_validate(response.json())
        codes = {item.kcr_code for item in page.items}
        if (
            page.batch_id != batch_id
            or page.offset != offset
            or (total is not None and page.total != total)
            or len(page.items) != min(page.limit, page.total - offset)
            or len(codes) != len(page.items)
            or not seen.isdisjoint(codes)
        ):
            raise ValueError("Несогласованный список затронутых клиентов")
        seen.update(codes)
        affected.extend(page.items)
        offset += len(page.items)
        total = page.total

    return affected


def admin_headers(actor: UUID) -> dict[str, str]:
    return {"X-User-Id": str(actor), "X-User-Role": "ADMIN"}


async def prepare_deliveries(client: httpx.AsyncClient, batch_id: UUID) -> list[OperatorDelivery]:
    clients = [
        {"kcr_code": item.kcr_code, "outstanding_documents": item.outstanding_documents}
        for item in await affected_clients(client, batch_id)
        if item.outstanding_documents is not None
    ]
    count = (len(clients) + PART_SIZE - 1) // PART_SIZE
    if count > MAX_PARTS:
        raise ValueError("Пакет превышает лимит частей Operator Work")
    deliveries = []
    for number, start in enumerate(range(0, len(clients), PART_SIZE), start=1):
        payload = {"batch_id": str(batch_id), "clients": clients[start : start + PART_SIZE]}
        if count > 1:
            payload["delivery"] = {
                "delivery_id": str(uuid4()),
                "part_number": number,
                "part_count": count,
            }
        deliveries.append(OperatorDelivery(batch_id=batch_id, part_number=number, payload=payload))
    return deliveries


async def send_delivery(
    client: httpx.AsyncClient, quarter: str, uploaded_by: UUID, payload: dict
) -> int | None:
    response = await client.put(
        "/internal/v1/cycles/apply-snapshot",
        params={"work_quarter": quarter},
        headers=admin_headers(uploaded_by),
        json=payload,
    )
    if response.status_code == 204:
        return None
    if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
        return response.status_code
    response.raise_for_status()
    raise ValueError("Operator Work не подтвердил применение среза")
