import asyncio
import functools
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest

from sweeper.loop import sweep_until_stopped
from sweeper.sweeper import Sweeper

pytestmark = pytest.mark.asyncio

# Long enough that a test only returns in time if the stop cuts the wait short.
LONG_INTERVAL_SECONDS = 3600
RETURN_DEADLINE_SECONDS = 5
STOP_DELAY_SECONDS = 0.05
NO_INTERVAL_SECONDS = 0


def _build_sweeper() -> AsyncMock:
    return cast(AsyncMock, create_autospec(Sweeper, instance=True))


async def _pass_interrupted_by_stop(stop: asyncio.Event, finished_passes: list[int]) -> None:
    stop.set()
    # Gives the loop the chance to act on the stop before the pass finishes.
    await asyncio.sleep(0)
    finished_passes.append(len(finished_passes) + 1)


async def _fail_first_then_stop(stop: asyncio.Event, sweeper: AsyncMock) -> None:
    if sweeper.sweep.await_count == 1:
        raise ConnectionError("Postgres unreachable")
    stop.set()


async def test_a_stop_during_a_pass_lets_the_pass_finish_then_returns(tmp_path: Path) -> None:
    sweeper = _build_sweeper()
    stop = asyncio.Event()
    finished_passes: list[int] = []
    sweeper.sweep.side_effect = functools.partial(_pass_interrupted_by_stop, stop, finished_passes)
    heartbeat = tmp_path / "heartbeat"

    async with asyncio.timeout(RETURN_DEADLINE_SECONDS):
        await sweep_until_stopped(sweeper, stop, LONG_INTERVAL_SECONDS, heartbeat)

    assert finished_passes == [1]
    assert heartbeat.exists()


async def test_a_stop_between_passes_returns_without_waiting_out_the_interval(
    tmp_path: Path,
) -> None:
    sweeper = _build_sweeper()
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(STOP_DELAY_SECONDS, stop.set)

    async with asyncio.timeout(RETURN_DEADLINE_SECONDS):
        await sweep_until_stopped(sweeper, stop, LONG_INTERVAL_SECONDS, tmp_path / "heartbeat")

    sweeper.sweep.assert_awaited_once()


async def test_a_failed_pass_is_retried_on_the_next_interval(tmp_path: Path) -> None:
    sweeper = _build_sweeper()
    stop = asyncio.Event()
    sweeper.sweep.side_effect = functools.partial(_fail_first_then_stop, stop, sweeper)
    heartbeat = tmp_path / "heartbeat"

    async with asyncio.timeout(RETURN_DEADLINE_SECONDS):
        await sweep_until_stopped(sweeper, stop, NO_INTERVAL_SECONDS, heartbeat)

    assert sweeper.sweep.await_count == 2
    assert heartbeat.exists()
