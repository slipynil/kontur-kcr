import os
from dataclasses import dataclass, field
from typing import Self


@dataclass(frozen=True)
class Settings:
    # The URL carries credentials: it stays out of repr and logs.
    database_url: str = field(repr=False)
    gateway_url: str | None = None
    clients_url: str | None = None
    upstream_timeout: float = 3.0

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            database_url=os.environ["CLIENT_HISTORY_DATABASE_URL"],
            gateway_url=os.environ.get("CLIENT_HISTORY_GATEWAY_URL") or None,
            clients_url=os.environ.get("CLIENT_HISTORY_CLIENTS_URL") or None,
            upstream_timeout=float(os.environ.get("CLIENT_HISTORY_UPSTREAM_TIMEOUT", 3)),
        )
