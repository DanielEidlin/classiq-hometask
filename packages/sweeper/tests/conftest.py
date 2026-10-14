import asyncio
import sys
from collections.abc import AsyncIterator, Iterator
from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer

from common.data.publisher import TaskPublisher
from common.data.tables import metadata

POSTGRES_IMAGE = "postgres:16-alpine"


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    # psycopg's async mode can't run on Windows' default (Proactor) event loop.
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A throwaway Postgres with the task table, for the whole session."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        engine = sa.create_engine(url)
        metadata.create_all(engine)
        engine.dispose()
        yield url


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.execute(sa.text("TRUNCATE tasks"))
        await engine.dispose()


@pytest.fixture
def publisher() -> AsyncMock:
    return cast(AsyncMock, create_autospec(TaskPublisher, instance=True))
