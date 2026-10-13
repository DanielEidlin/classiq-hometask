"""The async publisher shared by the API and the sweeper."""

import uuid
from typing import Self

import aio_pika
import aio_pika.abc

from common.message import TASK_QUEUE, encode_task_message


class TaskPublisher:
    """Publishes task messages to the task queue, waiting for the broker's confirmation.

    Build one with `TaskPublisher.connect(url)`. The constructor takes the connection and
    channel, so tests can pass their own.
    """

    def __init__(
        self,
        connection: aio_pika.abc.AbstractRobustConnection,
        channel: aio_pika.abc.AbstractChannel,
    ) -> None:
        self._connection = connection
        self._channel = channel

    @classmethod
    async def connect(cls, url: str) -> Self:
        """Open a robust connection (aio-pika reconnects on its own after the broker restarts)
        and a channel with publisher confirms, and declare the task queue."""
        connection = await aio_pika.connect_robust(url)
        channel = await connection.channel(publisher_confirms=True)
        await channel.declare_queue(TASK_QUEUE, durable=True)
        return cls(connection, channel)

    async def close(self) -> None:
        await self._connection.close()

    @property
    def is_connected(self) -> bool:
        # `is_closed` only flips on an explicit close(); `connected` is cleared whenever the link drops.
        return self._connection.connected.is_set()

    async def publish(self, task_id: uuid.UUID) -> None:
        """Publish the task's message; returns once the broker has confirmed it."""
        await self._channel.default_exchange.publish(
            encode_task_message(task_id), routing_key=TASK_QUEUE
        )
