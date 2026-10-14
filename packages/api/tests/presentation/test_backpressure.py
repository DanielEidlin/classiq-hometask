import uuid
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.presentation.schemas import SERVICE_UNAVAILABLE_MESSAGE
from api.settings import RETRY_AFTER_SECONDS
from common.data.publisher import PublishFailed
from tests.support.fakes.api_task_store import FakeTaskStore

CIRCUIT = "OPENQASM 3.0;"


def test_failed_publish_returns_503_with_retry_after(
    client: TestClient, publisher: AsyncMock
) -> None:
    publisher.publish.side_effect = PublishFailed("rejected")

    response = client.post("/tasks", json={"qc": CIRCUIT})

    assert response.status_code == 503
    assert response.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
    assert response.json() == {"status": "error", "message": SERVICE_UNAVAILABLE_MESSAGE}


def test_failed_publish_deletes_the_inserted_task(
    client: TestClient, task_store: FakeTaskStore, publisher: AsyncMock
) -> None:
    publisher.publish.side_effect = PublishFailed("rejected")

    client.post("/tasks", json={"qc": CIRCUIT})

    assert task_store.deleted == task_store.inserted
    assert task_store.marked_published == []


def test_successful_publish_marks_the_task_published_and_keeps_it(
    client: TestClient, task_store: FakeTaskStore
) -> None:
    response = client.post("/tasks", json={"qc": CIRCUIT})

    assert response.status_code == 202
    task_id = uuid.UUID(response.json()["task_id"])
    assert task_store.inserted == task_store.marked_published == [task_id]
    assert task_store.deleted == []

