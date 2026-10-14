"""The sweeper's main loop: a pass every interval until asked to stop."""

import asyncio
import contextlib
from pathlib import Path

import structlog

from sweeper.business.sweeper import Sweeper
from sweeper.data.heartbeat import record_pass

log = structlog.get_logger()


async def sweep_until_stopped(
    sweeper: Sweeper, stop_event: asyncio.Event, interval_seconds: float, heartbeat: Path
) -> None:
    """Run a pass every `interval_seconds`, recording each successful one, until `stop_event`
    is set.

    A pass in progress when `stop_event` is set runs to the end, so its row locks are released
    by a commit rather than a dropped connection; the wait between passes is cut short.
    """
    while not stop_event.is_set():
        try:
            await sweeper.sweep()
        except Exception:
            # A failed pass (e.g. Postgres unreachable) is retried on the next interval;
            # the stale heartbeat lets the health check report it if it keeps failing.
            log.exception("sweep_failed")
        else:
            record_pass(heartbeat)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), interval_seconds)
