"""The async publisher shared by the API and the sweeper."""

import asyncio
import uuid
from typing import Self

import aio_pika
import aio_pika.abc
import aio_pika.exceptions

from common.task_queue import TASK_QUEUE, TASK_QUEUE_ARGUMENTS, encode_task_message
from common.settings import PUBLISH_TIMEOUT_SECONDS


class PublishFailed(Exception):
    """The broker didn't confirm the message: it rejected it (the queue is full), the
    connection is down, or no confirmation arrived in time."""


class TaskPublisher:
    """Publishes task messages to the task queue, waiting for the broker's confirmation.

    Build one with `TaskPublisher.connect(url)`. The constructor takes the connection and
    channel, so tests can pass their own.
    """

    def __init__(
        self,
        connection: aio_pika.abc.AbstractRobustConnection,
        channel: aio_pika.abc.AbstractChannel,
        publish_timeout_seconds: float = PUBLISH_TIMEOUT_SECONDS,
    ) -> None:
        self._connection = connection
        self._channel = channel
        self._publish_timeout_seconds = publish_timeout_seconds

    @classmethod
    async def connect(cls, url: str) -> Self:
        """Open a robust connection (aio-pika reconnects on its own after the broker restarts)
        and a channel with publisher confirms, and declare the task queue."""
        connection = await aio_pika.connect_robust(url)
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        await channel.declare_queue(TASK_QUEUE, durable=True, arguments=TASK_QUEUE_ARGUMENTS)
        return cls(connection, channel)

    async def close(self) -> None:
        await self._connection.close()

    @property
    def is_connected(self) -> bool:
        # `is_closed` only flips on an explicit close(); `connected` is cleared whenever the link drops.
        return self._connection.connected.is_set()

    async def publish(self, task_id: uuid.UUID) -> None:
        """Publish the task's message; returns once the broker has confirmed it.

        Raises `PublishFailed` otherwise. A timed-out message may still reach the queue later.
        """
        try:
            async with asyncio.timeout(self._publish_timeout_seconds):
                await self._channel.default_exchange.publish(
                    encode_task_message(task_id), routing_key=TASK_QUEUE
                )
        except (
            aio_pika.exceptions.AMQPError,
            aio_pika.exceptions.ChannelInvalidStateError,
            ConnectionError,
            TimeoutError,
        ) as error:
            raise PublishFailed(f"task {task_id} was not confirmed: {repr(error)}") from error
