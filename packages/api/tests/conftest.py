from collections.abc import Iterator
from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncConnection

from api.dependencies import get_db_connection, get_publisher
from api.main import app
from common.publisher import TaskPublisher


@pytest.fixture
def db_connection() -> AsyncMock:
    return cast(AsyncMock, create_autospec(AsyncConnection, instance=True))


@pytest.fixture
def publisher() -> AsyncMock:
    return cast(AsyncMock, create_autospec(TaskPublisher, instance=True))


@pytest.fixture
def client(db_connection: AsyncMock, publisher: AsyncMock) -> Iterator[TestClient]:
    """The app in-process, with the database and the broker replaced by mocks.

    Not entering TestClient as a context manager skips the lifespan, so no real database
    or broker is needed.
    """
    app.dependency_overrides[get_db_connection] = lambda: db_connection
    app.dependency_overrides[get_publisher] = lambda: publisher
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()
