"""One pass of the sweeper: publish tasks the API stored but never published, and report
tasks that have been pending too long."""

from datetime import timedelta

import structlog

from common.publisher import TaskPublisher
from sweeper.abstractions import TaskStore

log = structlog.get_logger()


class Sweeper:
    def __init__(
        self, store: TaskStore, publisher: TaskPublisher, grace: timedelta, stale_after: timedelta
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._grace = grace
        self._stale_after = stale_after

    async def sweep(self) -> None:
        await self._publish_unpublished()
        await self._report_stale()

    async def _publish_unpublished(self) -> None:
        async with self._store.lock_unpublished(self._grace) as batch:
            for task_id in batch.task_ids:
                try:
                    await self._publisher.publish(task_id)
                except Exception:
                    # Rejected (queue full) or the broker is unreachable: the task stays
                    # unpublished, and the next pass tries again.
                    log.warning("publish_failed", task_id=str(task_id), exc_info=True)
                    continue
                await batch.mark_published(task_id)
                log.info("task_published", task_id=str(task_id))

    async def _report_stale(self) -> None:
        """Warn about tasks pending too long. They are not republished: a task legitimately
        waiting in a long queue looks the same as one whose message was lost (decision 18)."""
        for stale in await self._store.find_stale(self._stale_after):
            log.warning(
                "stale_task",
                task_id=str(stale.id),
                pending_for_seconds=int(stale.pending_for.total_seconds()),
            )
