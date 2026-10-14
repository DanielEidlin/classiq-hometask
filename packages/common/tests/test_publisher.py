import asyncio
import uuid
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import aio_pika.abc
import pytest
from aio_pika.exceptions import ChannelInvalidStateError, DeliveryError
from pamqp.commands import Basic

from common.publisher import PublishFailed, TaskPublisher

# Short, so the timeout case doesn't slow the suite down.
PUBLISH_TIMEOUT_SECONDS = 0.05
# Far past the timeout, so the publish is cancelled long before it would return.
NEVER_CONFIRMED_SECONDS = PUBLISH_TIMEOUT_SECONDS * 100


def _publisher_with_publish(publish: AsyncMock) -> TaskPublisher:
    channel = MagicMock()
    channel.default_exchange.publish = publish
    return TaskPublisher(
        cast(aio_pika.abc.AbstractRobustConnection, MagicMock()),
        cast(aio_pika.abc.AbstractChannel, channel),
        publish_timeout_seconds=PUBLISH_TIMEOUT_SECONDS,
    )


async def _never_confirmed(*args: object, **kwargs: object) -> None:
    await asyncio.sleep(NEVER_CONFIRMED_SECONDS)


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(DeliveryError(None, Basic.Nack()), id="rejected"),
        pytest.param(ChannelInvalidStateError("closed"), id="channel closed"),
        pytest.param(ConnectionResetError(), id="connection lost"),
    ],
)
def test_publish_failure_raises_publish_failed(failure: Exception) -> None:
    publisher = _publisher_with_publish(AsyncMock(side_effect=failure))

    with pytest.raises(PublishFailed):
        asyncio.run(publisher.publish(uuid.uuid4()))


def test_unconfirmed_publish_times_out_as_publish_failed() -> None:
    publisher = _publisher_with_publish(AsyncMock(side_effect=_never_confirmed))

    with pytest.raises(PublishFailed):
        asyncio.run(publisher.publish(uuid.uuid4()))


def test_confirmed_publish_returns() -> None:
    publish = AsyncMock()
    publisher = _publisher_with_publish(publish)

    asyncio.run(publisher.publish(uuid.uuid4()))

    publish.assert_awaited_once()
