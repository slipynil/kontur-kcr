import asyncio
import os

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine


def migrate(connection: Connection) -> None:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run() -> None:
    engine = create_async_engine(os.environ["IMPORT_DATABASE_URL"], hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(dialect_name="postgresql", literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run())
