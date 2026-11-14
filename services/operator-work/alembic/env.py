import os

from alembic import context
from sqlalchemy import create_engine

url = os.environ["OPERATOR_WORK_DATABASE_URL"]

if context.is_offline_mode():
    context.configure(url=url)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
