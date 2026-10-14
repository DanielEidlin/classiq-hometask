import uuid

from fastapi.testclient import TestClient

from api.main import INTERNAL_SERVER_ERROR_MESSAGE, app
from api.presentation.dependencies import get_task_store
from tests.support.fakes.api_task_store import BrokenTaskStore


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
        ("POST", "/tasks"): {"202", "400", "413", "503"},
        ("GET", "/tasks/{task_id}"): {"200", "404"},
        ("GET", "/health"): {"200", "503"},
    }
