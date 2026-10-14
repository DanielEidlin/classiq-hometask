import os

import sqlalchemy as sa
from alembic import context

from common.data.tables import metadata

config = context.config
if config.config_file_name is not None:
    from logging.config import fileConfig

    fileConfig(config.config_file_name)


def run_migrations_online() -> None:
    engine = sa.create_engine(os.environ["DATABASE_URL"], poolclass=sa.pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
