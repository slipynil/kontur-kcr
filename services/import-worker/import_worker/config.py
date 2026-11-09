import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass(frozen=True)
class Settings:
    database_url: str
    amqp_url: str
    upload_dir: Path
    max_upload_bytes: int = 32 * 1024**2
    clients_url: str | None = None

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            database_url=os.environ["IMPORT_DATABASE_URL"],
            amqp_url=os.environ["IMPORT_AMQP_URL"],
            upload_dir=Path(os.environ["IMPORT_UPLOAD_DIR"]),
            max_upload_bytes=int(os.environ.get("IMPORT_MAX_UPLOAD_BYTES", 32 * 1024**2)),
            clients_url=os.environ.get("IMPORT_LOCAL_CLIENTS_URL") or None,
        )
