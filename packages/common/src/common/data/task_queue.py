"""Defines the task queue and its message, which the publishers (API, sweeper) and the Celery
worker agree on.

The queue's name and arguments are shared because every declaration must match: RabbitMQ
refuses a declaration whose arguments differ from the existing queue's. The task name is
shared so the message names the task the worker registers.

The message carries only the task ID. It is built in Celery's task message format
(protocol v2, https://docs.celeryq.dev/en/stable/internals/protocol.html) so that a
Celery worker consumes it natively, without the publishers depending on Celery. Only the
publishers run this encoder; Celery parses the message on the worker. The worker's tests
check that the message names a registered task and fits its signature, and the integration
tests check that a real worker consumes it.
"""

import json
import uuid

import aio_pika

from common.settings import MAX_QUEUED_TASKS

PROCESS_TASK_NAME = "worker.process"
TASK_QUEUE = "tasks"
TASK_QUEUE_ARGUMENTS: dict[str, object] = {
    "x-max-length": MAX_QUEUED_TASKS,
    "x-overflow": "reject-publish",
}


def encode_task_message(task_id: uuid.UUID) -> aio_pika.Message:
    """Build the protocol v2 message asking a worker to process `task_id`.

    Only the headers the protocol marks as required are set; Celery treats the optional
    ones (eta, expires, retries, time limits, ...) as absent. The Celery task ID is the
    task's own ID, so worker logs and the database share one key.
    """
    celery_id = str(task_id)
    args = [str(task_id)]
    kwargs: dict[str, object] = {}
    embed = {"callbacks": None, "errbacks": None, "chain": None, "chord": None}
    return aio_pika.Message(
        body=json.dumps([args, kwargs, embed]).encode("utf-8"),
        headers={
            "lang": "py",
            "task": PROCESS_TASK_NAME,
            "id": celery_id,
            "root_id": celery_id,
            "parent_id": None,
            "group": None,
        },
        content_type="application/json",
        content_encoding="utf-8",
        correlation_id=celery_id,
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
