import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.main import DATABASE_UNAVAILABLE_MESSAGE, INTERNAL_SERVER_ERROR_MESSAGE, app
from api.presentation.dependencies import get_task_store
from api.settings import RETRY_AFTER_SECONDS
from tests.support.fakes.api_task_store import BrokenTaskStore, UnavailableTaskStore

CIRCUIT = "OPENQASM 3.0;"


def test_unexpected_error_returns_500_in_the_error_shape(client: TestClient) -> None:
    app.dependency_overrides[get_task_store] = lambda: BrokenTaskStore()

    response = client.get(f"/tasks/{uuid.uuid4()}")

    assert response.status_code == 500
    assert response.json() == {"status": "error", "message": INTERNAL_SERVER_ERROR_MESSAGE}


def test_api_docs_list_the_error_responses_and_not_fastapis_422(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    documented = {
        (method.upper(), path): set(operation["responses"])
        for path, operations in paths.items()
        for method, operation in operations.items()
    }
    assert documented == {
        ("POST", "/tasks"): {"202", "400", "413", "500", "503"},
        ("GET", "/tasks/{task_id}"): {"200", "404", "500", "503"},
        ("GET", "/health"): {"200", "500", "503"},
    }


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        pytest.param("POST", "/tasks", {"qc": CIRCUIT}, id="submit"),
        pytest.param("GET", f"/tasks/{uuid.uuid4()}", None, id="get"),
    ],
)
def test_an_unreachable_database_returns_503_with_retry_after(
    client: TestClient,
    publisher: AsyncMock,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    app.dependency_overrides[get_task_store] = lambda: UnavailableTaskStore()

    response = client.request(method, path, json=body)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert response.json() == {"status": "error", "message": DATABASE_UNAVAILABLE_MESSAGE}
    publisher.publish.assert_not_awaited()
