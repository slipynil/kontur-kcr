from collections import OrderedDict
from collections.abc import Iterable
from urllib.parse import quote
from uuid import UUID

import httpx

from client_history.errors import unavailable

# The Gateway accepts at most 100 ids per request (docs/api/internal/gateway-identity.yaml).
USERS_PAGE = 100


class Names:
    """Logins of event authors from the Gateway internal API, remembered per process."""

    def __init__(self, client: httpx.Client | None, capacity: int = 10_000) -> None:
        self.client = client
        self.capacity = capacity
        self._cache: OrderedDict[UUID, str | None] = OrderedDict()

    def resolve(self, actor_ids: Iterable[UUID]) -> dict[UUID, str]:
        """Known logins keyed by actor; an unknown user is simply absent."""
        requested = set(actor_ids)
        missing = sorted(requested - self._cache.keys())
        for start in range(0, len(missing), USERS_PAGE):
            page = missing[start : start + USERS_PAGE]
            found = self._fetch(page)
            # A user missing from the response stays unknown: remember that too.
            for actor_id in page:
                self._remember(actor_id, found.get(actor_id))
        return {
            actor_id: self._cache[actor_id]
            for actor_id in requested
            if self._cache.get(actor_id) is not None
        }

    def _fetch(self, actor_ids: list[UUID]) -> dict[UUID, str | None]:
        if self.client is None:
            raise unavailable("Адрес Gateway не задан: имя автора определить нельзя")
        try:
            response = self.client.get(
                "/internal/v1/users", params={"ids": ",".join(map(str, actor_ids))}
            )
        except httpx.HTTPError as exc:
            raise unavailable("Gateway временно недоступен") from exc
        if response.status_code != 200:
            raise unavailable("Gateway временно недоступен")
        try:
            found: dict[UUID, str | None] = {}
            for item in response.json()["items"]:
                found[UUID(str(item["id"]))] = item.get("display_name") or item.get("login") or None
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise unavailable("Gateway вернул некорректный ответ") from exc
        return found

    def _remember(self, actor_id: UUID, name: str | None) -> None:
        self._cache[actor_id] = name
        self._cache.move_to_end(actor_id)
        while len(self._cache) > self.capacity:
            self._cache.popitem(last=False)


class Registry:
    """Whether a code exists in the Clients registry; None when the service cannot tell."""

    def __init__(self, client: httpx.Client | None) -> None:
        self.client = client

    def exists(self, code: str) -> bool | None:
        if self.client is None:
            return None
        try:
            response = self.client.get("/internal/v1/clients/" + quote(code, safe=""))
        except httpx.HTTPError as exc:
            raise unavailable("Clients Service временно недоступен") from exc
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise unavailable("Clients Service временно недоступен")
