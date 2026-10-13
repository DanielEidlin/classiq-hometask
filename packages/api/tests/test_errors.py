import uuid
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncConnection

from api.dependencies import get_db_connection
from api.main import app


async def _broken_db_connection() -> AsyncIterator[AsyncConnection]:
    raise RuntimeError("the database driver blew up")
    yield  # pragma: no cover - makes this an async generator, like the real dependency


def test_unexpected_error_returns_500_in_the_error_shape() -> None:
    app.dependency_overrides[get_db_connection] = _broken_db_connection
    try:
        # Not entering TestClient as a context manager skips the lifespan, so no real
        # database or broker is needed.
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(f"/tasks/{uuid.uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json() == {"status": "error", "message": "Internal server error."}
