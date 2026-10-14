"""The API's task store on Postgres.

Each call checks a connection out of the engine's pool and returns it, so no connection is
held while a request waits on something else, such as a publish.
"""

import uuid

import sqlalchemy as sa
import structlog
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncEngine

from api.domain.outcomes import TaskOutcome
from common.data.tables import tasks

log = structlog.get_logger()

_task_outcome = TypeAdapter[TaskOutcome](TaskOutcome)


class PostgresTaskStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def insert_task(self, task_id: uuid.UUID, circuit: str) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(tasks.insert().values(id=task_id, circuit=circuit))

    async def mark_published(self, task_id: uuid.UUID) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                tasks.update().where(tasks.c.id == task_id).values(published=True)
            )

    async def delete_task(self, task_id: uuid.UUID) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(tasks.delete().where(tasks.c.id == task_id))

    async def fetch_outcome(self, task_id: uuid.UUID) -> TaskOutcome | None:
        query = sa.select(tasks.c.status, tasks.c.result, tasks.c.error_message).where(
            tasks.c.id == task_id
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(query)).one_or_none()
        return None if row is None else _task_outcome.validate_python(dict(row._mapping))

    async def is_reachable(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(sa.text("SELECT 1"))
        except Exception:
            log.warning("database_unreachable", exc_info=True)
            return False
        return True
