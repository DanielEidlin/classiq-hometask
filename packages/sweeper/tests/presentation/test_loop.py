import asyncio
import functools
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest

from sweeper.business.sweeper import Sweeper
from sweeper.presentation.loop import sweep_until_stopped

pytestmark = pytest.mark.asyncio

# Long enough that a test only returns in time if the stop cuts the wait short.
LONG_INTERVAL_SECONDS = 3600
RETURN_DEADLINE_SECONDS = 5
STOP_DELAY_SECONDS = 0.05
# Long enough for a loop that cancelled the pass on the stop to do so.
PASS_DURATION_SECONDS = 0.05
NO_INTERVAL_SECONDS = 0


def _build_sweeper() -> AsyncMock:
    return cast(AsyncMock, create_autospec(Sweeper, instance=True))


async def _pass_outlasting_the_stop(
    stop_event: asyncio.Event, pass_finished: asyncio.Event
) -> None:
    """A pass during which the stop arrives. A loop that cancelled the pass on the stop would
    cancel it during the sleep, and `pass_finished` would never be set."""
    stop_event.set()
    await asyncio.sleep(PASS_DURATION_SECONDS)
    pass_finished.set()


async def _fail_first_then_stop(stop_event: asyncio.Event, sweeper: AsyncMock) -> None:
    if sweeper.sweep.await_count == 1:
        raise ConnectionError("Postgres unreachable")
    stop_event.set()


async def test_a_stop_during_a_pass_lets_the_pass_finish_then_returns(tmp_path: Path) -> None:
    sweeper = _build_sweeper()
    stop_event = asyncio.Event()
    pass_finished = asyncio.Event()
    sweeper.sweep.side_effect = functools.partial(
        _pass_outlasting_the_stop, stop_event, pass_finished
    )
    heartbeat = tmp_path / "heartbeat"

    async with asyncio.timeout(RETURN_DEADLINE_SECONDS):
        await sweep_until_stopped(sweeper, stop_event, LONG_INTERVAL_SECONDS, heartbeat)

    assert pass_finished.is_set()
    assert heartbeat.exists()
    sweeper.sweep.assert_awaited_once()


async def test_a_stop_between_passes_returns_without_waiting_out_the_interval(
    tmp_path: Path,
) -> None:
    sweeper = _build_sweeper()
    stop_event = asyncio.Event()
    asyncio.get_running_loop().call_later(STOP_DELAY_SECONDS, stop_event.set)

    async with asyncio.timeout(RETURN_DEADLINE_SECONDS):
        await sweep_until_stopped(
            sweeper, stop_event, LONG_INTERVAL_SECONDS, tmp_path / "heartbeat"
        )

    sweeper.sweep.assert_awaited_once()


async def test_a_failed_pass_is_retried_on_the_next_interval(tmp_path: Path) -> None:
    sweeper = _build_sweeper()
    stop_event = asyncio.Event()
    sweeper.sweep.side_effect = functools.partial(_fail_first_then_stop, stop_event, sweeper)
    heartbeat = tmp_path / "heartbeat"

    async with asyncio.timeout(RETURN_DEADLINE_SECONDS):
        await sweep_until_stopped(sweeper, stop_event, NO_INTERVAL_SECONDS, heartbeat)

    assert sweeper.sweep.await_count == 2
    assert heartbeat.exists()
