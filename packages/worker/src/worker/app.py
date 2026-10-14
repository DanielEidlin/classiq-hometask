"""The Celery application consuming the task queue."""

import os

from celery import Celery
from kombu import Queue

from common.log import configure_logging
from common.task_queue import TASK_QUEUE, TASK_QUEUE_ARGUMENTS

configure_logging("worker")

app = Celery("worker", broker=os.environ["RABBITMQ_URL"], include=["worker.tasks"])
app.conf.update(
    task_default_queue=TASK_QUEUE,
    task_queues=[Queue(TASK_QUEUE, durable=True, queue_arguments=TASK_QUEUE_ARGUMENTS)],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=int(os.environ.get("WORKER_CONCURRENCY", "1")),
    broker_connection_retry_on_startup=True,
    # structlog writes JSON lines to stdout itself; Celery would wrap each one in its own log line.
    worker_redirect_stdouts=False,
)
