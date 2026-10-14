from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from common.data.tables import metadata

POSTGRES_IMAGE = "postgres:16-alpine"


@pytest.fixture(scope="session")
def db_engine() -> Iterator[sa.Engine]:
    """A throwaway Postgres with the task table, for the whole session."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        engine = sa.create_engine(postgres.get_connection_url())
        metadata.create_all(engine)
        try:
            yield engine
        finally:
            engine.dispose()
