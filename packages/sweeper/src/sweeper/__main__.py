"""Runs the sweeper: a pass every `SWEEP_INTERVAL_SECONDS`, recording each successful one."""

import asyncio
import os

import structlog
from sqlalchemy.ext.asyncio import create_async_engine

from common.log import configure_logging
from common.publisher import TaskPublisher
from sweeper.health import record_pass
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
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    publisher = await TaskPublisher.connect(os.environ["RABBITMQ_URL"])
    sweeper = Sweeper(engine, publisher, grace=SWEEP_GRACE, stale_after=STALE_TASK_AFTER)
    try:
        while True:
            try:
                await sweeper.sweep()
            except Exception:
                # A failed pass (e.g. Postgres unreachable) is retried on the next interval;
                # the stale heartbeat lets the health check report it if it keeps failing.
                log.exception("sweep_failed")
            else:
                record_pass(SWEEPER_HEARTBEAT_FILE)
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
    finally:
        await publisher.close()
        await engine.dispose()


asyncio.run(run())
