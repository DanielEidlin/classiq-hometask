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
