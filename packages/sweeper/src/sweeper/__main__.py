"""Runs the sweeper until SIGTERM or SIGINT, then finishes the current pass and exits."""

import asyncio
import os
import signal

import structlog
from sqlalchemy.ext.asyncio import create_async_engine

from common.log import configure_logging
from common.publisher import TaskPublisher
from sweeper.loop import sweep_until_stopped
from sweeper.settings import (
    STALE_TASK_AFTER,
    SWEEP_GRACE,
    SWEEP_INTERVAL_SECONDS,
    SWEEPER_HEARTBEAT_FILE,
)
from sweeper.sweeper import Sweeper

log = structlog.get_logger()


async def run() -> None:
    configure_logging("sweeper")
    # As PID 1 in its container, the process would otherwise ignore SIGTERM and be killed
    # once Docker's stop grace period ran out.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signal_number, stop_event.set)

    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    publisher = await TaskPublisher.connect(os.environ["RABBITMQ_URL"])
    sweeper = Sweeper(engine, publisher, grace=SWEEP_GRACE, stale_after=STALE_TASK_AFTER)
    try:
        await sweep_until_stopped(
            sweeper, stop_event, SWEEP_INTERVAL_SECONDS, SWEEPER_HEARTBEAT_FILE
        )
    finally:
        await publisher.close()
        await engine.dispose()
    log.info("sweeper_stopped")


asyncio.run(run())
