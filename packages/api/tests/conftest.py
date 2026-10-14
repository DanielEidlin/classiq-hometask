import asyncio
import sys
from collections.abc import AsyncIterator, Iterator
from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer

from api.main import app
from api.presentation.dependencies import get_publisher, get_task_store
from common.data.publisher import TaskPublisher
from common.data.tables import metadata
from tests.support.fakes.api_task_store import FakeTaskStore

POSTGRES_IMAGE = "postgres:16-alpine"


@pytest.fixture
def task_store() -> FakeTaskStore:
    return FakeTaskStore()


@pytest.fixture
def publisher() -> AsyncMock:
    return cast(AsyncMock, create_autospec(TaskPublisher, instance=True))


@pytest.fixture
def client(task_store: FakeTaskStore, publisher: AsyncMock) -> Iterator[TestClient]:
    """The app in-process, with the task store and the broker replaced.

    Not entering TestClient as a context manager skips the lifespan, so no real database
    or broker is needed.
    """
    app.dependency_overrides[get_task_store] = lambda: task_store
    app.dependency_overrides[get_publisher] = lambda: publisher
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


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
