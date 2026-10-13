"""The async publisher shared by the API and the sweeper."""

import uuid

import aio_pika
import aio_pika.abc

from common.message import TASK_QUEUE, encode_task_message


class TaskPublisher:
    """Publishes task messages to the task queue, waiting for the broker's confirmation.

    The connection is robust: aio-pika reconnects on its own after the broker restarts.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel(publisher_confirms=True)
        await self._channel.declare_queue(TASK_QUEUE, durable=True)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()

    @property
    def is_connected(self) -> bool:
        # `is_closed` only flips on an explicit close(); `connected` is cleared whenever the link drops.
        return self._connection is not None and self._connection.connected.is_set()

    async def publish(self, task_id: uuid.UUID) -> None:
        """Publish the task's message; returns once the broker has confirmed it."""
        if self._channel is None:
            raise RuntimeError("TaskPublisher.connect() has not been called")
        await self._channel.default_exchange.publish(
            encode_task_message(task_id), routing_key=TASK_QUEUE
        )
