import os

from alembic import context
from sqlalchemy import create_engine

url = os.environ["ERRORS_HISTORY_DATABASE_URL"]

if context.is_offline_mode():
    context.configure(url=url)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
