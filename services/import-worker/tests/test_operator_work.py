import json
from uuid import UUID, uuid4

import httpx
import pytest

from import_worker import operator_work


async def test_snapshot_delivery_preserves_zero_and_splits_at_the_part_limit() -> None:
    batch_id = uuid4()
    clients = [
        {"kcr_code": f"KCR-{index:04}", "outstanding_documents": index}
        for index in range(operator_work.PART_SIZE + 1)
    ]
    affected = [*clients, {"kcr_code": "REMOVED", "outstanding_documents": None}]
    offsets = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/internal/v1/import/{batch_id}/affected-clients"
        assert int(request.url.params["limit"]) == operator_work.PART_SIZE
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        return httpx.Response(
            200,
            json={
                "batch_id": str(batch_id),
                "offset": offset,
                "limit": operator_work.PART_SIZE,
                "total": len(affected),
                "items": affected[offset : offset + operator_work.PART_SIZE],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://clients"
    ) as client:
        parts = await operator_work.prepare_deliveries(client, batch_id)

    assert offsets == [0, operator_work.PART_SIZE]
    assert [part.part_number for part in parts] == [1, 2]
    assert [len(part.payload["clients"]) for part in parts] == [operator_work.PART_SIZE, 1]
    assert [row for part in parts for row in part.payload["clients"]] == clients
    identifiers = set()
    for part in parts:
        assert part.batch_id == batch_id
        assert part.payload["batch_id"] == str(batch_id)
        metadata = part.payload["delivery"]
        assert metadata["part_count"] == 2
        assert metadata["part_number"] == part.part_number
        identifiers.add(UUID(metadata["delivery_id"]))
    assert len(identifiers) == 2


@pytest.mark.parametrize("fault", ["batch", "offset", "total", "short_page", "duplicate"])
async def test_affected_clients_rejects_inconsistent_pages(fault: str) -> None:
    batch_id = uuid4()

    def respond(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        page = {
            "batch_id": str(batch_id),
            "offset": offset,
            "limit": 1,
            "total": 2,
            "items": [{"kcr_code": str(offset), "outstanding_documents": 1}],
        }
        if offset == 1:
            changes = {
                "batch": {"batch_id": str(uuid4())},
                "offset": {"offset": 0},
                "total": {"total": 3},
                "short_page": {"items": []},
                "duplicate": {"items": [{"kcr_code": "0", "outstanding_documents": 1}]},
            }
            page.update(changes[fault])
        return httpx.Response(200, json=page)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://clients"
    ) as client:
        with pytest.raises(ValueError, match="Несогласованный список"):
            await operator_work.affected_clients(client, batch_id)


async def test_snapshot_delivery_fails_before_persisting_too_many_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operator_work, "PART_SIZE", 1)
    monkeypatch.setattr(operator_work, "MAX_PARTS", 1)
    batch_id = uuid4()

    def respond(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "batch_id": str(batch_id),
                "offset": offset,
                "limit": 1,
                "total": 2,
                "items": [{"kcr_code": str(offset), "outstanding_documents": 3}],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://clients"
    ) as client:
        with pytest.raises(ValueError, match="лимит частей"):
            await operator_work.prepare_deliveries(client, batch_id)


@pytest.mark.parametrize("status", [204, 409, 408, 429, 503, 200])
async def test_operator_delivery_requires_explicit_confirmation(status: int) -> None:
    actor = uuid4()
    payload = {
        "batch_id": str(uuid4()),
        "clients": [{"kcr_code": "KCR-1", "outstanding_documents": 3}],
    }

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/internal/v1/cycles/apply-snapshot"
        assert request.url.params["work_quarter"] == "2026_Q2"
        assert request.headers["X-User-Id"] == str(actor)
        assert request.headers["X-User-Role"] == "ADMIN"
        assert json.loads(request.content) == payload
        return httpx.Response(status)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://operator-work"
    ) as client:
        if status in {204, 409}:
            assert await operator_work.send_delivery(client, "2026_Q2", actor, payload) == (
                None if status == 204 else 409
            )
        elif status == 200:
            with pytest.raises(ValueError, match="не подтвердил"):
                await operator_work.send_delivery(client, "2026_Q2", actor, payload)
        else:
            with pytest.raises(httpx.HTTPStatusError) as failure:
                await operator_work.send_delivery(client, "2026_Q2", actor, payload)
            assert failure.value.response.status_code == status
