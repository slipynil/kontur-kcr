from uuid import uuid4

import httpx
import pytest
from conftest import upstream

from client_history.errors import HistoryError
from client_history.upstreams import USERS_PAGE, Names, Registry


def test_names_are_requested_in_pages():
    ids = [uuid4() for _ in range(USERS_PAGE + 5)]
    pages = []

    def handler(request):
        requested = request.url.params["ids"].split(",")
        pages.append(requested)
        return httpx.Response(200, json={"items": [{"id": i, "login": f"u{i}"} for i in requested]})

    resolved = Names(upstream(handler)).resolve(ids)
    assert [len(page) for page in pages] == [USERS_PAGE, 5]
    assert len(resolved) == len(ids)


def test_an_unknown_user_is_not_asked_again():
    calls = []

    def handler(request):
        calls.append(request.url.params["ids"])
        return httpx.Response(200, json={"items": []})

    names = Names(upstream(handler))
    actor = uuid4()
    assert names.resolve([actor]) == {}
    assert names.resolve([actor]) == {}
    assert len(calls) == 1


def test_the_name_cache_evicts_the_oldest_entry():
    calls = []

    def handler(request):
        calls.append(request.url.params["ids"])
        return httpx.Response(200, json={"items": []})

    names = Names(upstream(handler), capacity=1)
    first, second = uuid4(), uuid4()
    names.resolve([first])
    names.resolve([second])
    names.resolve([first])
    assert len(calls) == 3


def test_an_empty_request_needs_no_gateway():
    assert Names(None).resolve([]) == {}


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(503),
        lambda request: httpx.Response(200, json={"unexpected": True}),
        lambda request: httpx.Response(200, text="not json"),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("down")),
    ],
)
def test_a_failing_gateway_is_reported_as_unavailable(handler):
    with pytest.raises(HistoryError) as info:
        Names(upstream(handler)).resolve([uuid4()])
    assert (info.value.status, info.value.code) == (503, "HISTORY_UNAVAILABLE")


def test_a_missing_gateway_address_is_reported_as_unavailable():
    with pytest.raises(HistoryError) as info:
        Names(None).resolve([uuid4()])
    assert info.value.status == 503


@pytest.mark.parametrize(("status", "expected"), [(200, True), (404, False)])
def test_registry_reports_the_presence_of_a_code(status, expected):
    def handler(request):
        assert request.url.raw_path == b"/internal/v1/clients/A%2FB"
        return httpx.Response(status)

    assert Registry(upstream(handler)).exists("A/B") is expected


def test_registry_cannot_tell_without_a_client():
    assert Registry(None).exists("6671-0042") is None


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("down")),
    ],
)
def test_a_failing_registry_is_reported_as_unavailable(handler):
    with pytest.raises(HistoryError) as info:
        Registry(upstream(handler)).exists("6671-0042")
    assert (info.value.status, info.value.code) == (503, "HISTORY_UNAVAILABLE")
