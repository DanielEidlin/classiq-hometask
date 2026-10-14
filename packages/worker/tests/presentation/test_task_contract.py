"""The worker's side of the task message defined in `common.data.task_queue`.

Celery parses the message itself, so these tests check that what the publishers send names a
task the worker registers and fits that task's signature. Compliance with Celery's message
protocol is covered by the integration tests, where a real worker consumes the message.
"""

import importlib
import inspect
import json
import uuid
from types import ModuleType

import pytest

from common.data.task_queue import encode_task_message

# `worker.app` reads it at import; building the Celery app doesn't connect to the broker.
UNUSED_RABBITMQ_URL = "amqp://guest:guest@127.0.0.1:1//"


@pytest.fixture
def worker_tasks(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("RABBITMQ_URL", UNUSED_RABBITMQ_URL)
    return importlib.import_module("worker.tasks")


def test_the_message_names_a_registered_task(worker_tasks: ModuleType) -> None:
    message = encode_task_message(uuid.uuid4())

    assert message.headers["task"] in worker_tasks.app.tasks


def test_the_message_arguments_fit_the_task(worker_tasks: ModuleType) -> None:
    message = encode_task_message(uuid.uuid4())
    args, kwargs, _ = json.loads(message.body)

    task = worker_tasks.app.tasks[message.headers["task"]]

    inspect.signature(task.run).bind(*args, **kwargs)
