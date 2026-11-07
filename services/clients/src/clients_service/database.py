from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from clients_service.config import Settings

SCHEMA_REVISION = "0003_recovery"


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_timeout=settings.database_connect_timeout,
        hide_parameters=True,
        connect_args={
            "connect_timeout": settings.database_connect_timeout,
            "options": f"-c statement_timeout={settings.database_statement_timeout_ms}",
        },
    )
