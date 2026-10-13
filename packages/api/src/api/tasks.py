"""Routes for submitting tasks and reading their outcomes."""

import uuid
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from api.dependencies import ConnectionDep, PublisherDep
from common.tasks import TaskStatus, tasks

log = structlog.get_logger()

router = APIRouter(prefix="/tasks", tags=["tasks"])


class SubmitTaskRequest(BaseModel):
    quantum_circuit: str = Field(alias="qc", description="The circuit, as QASM3 text.")


class SubmitTaskResponse(BaseModel):
    task_id: uuid.UUID
    message: str


class TaskOutcome(BaseModel):
    """The stored state of a task: its status and, once final, its counts or failure reason."""

    status: TaskStatus
    result: dict[str, int] | None
    error_message: str | None


async def _fetch_outcome(connection: AsyncConnection, task_id: uuid.UUID) -> TaskOutcome | None:
    query = sa.select(tasks.c.status, tasks.c.result, tasks.c.error_message).where(
        tasks.c.id == task_id
    )
    row = (await connection.execute(query)).one_or_none()
    return None if row is None else TaskOutcome.model_validate(row._mapping)


@router.post("", status_code=202)
async def submit_task(
    body: SubmitTaskRequest, connection: ConnectionDep, publisher: PublisherDep
) -> SubmitTaskResponse:
    task_id = uuid.uuid4()
    async with connection.begin():
        await connection.execute(tasks.insert().values(id=task_id, circuit=body.quantum_circuit))
    await publisher.publish(task_id)
    async with connection.begin():
        await connection.execute(tasks.update().where(tasks.c.id == task_id).values(published=True))
    log.info("task_accepted", task_id=str(task_id))
    return SubmitTaskResponse(task_id=task_id, message="Task submitted successfully.")


@router.get("/{task_id}")
async def get_task(task_id: str, connection: ConnectionDep) -> dict[str, Any]:
    try:
        parsed_id = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(404, "Task not found.") from None

    outcome = await _fetch_outcome(connection, parsed_id)
    if outcome is None:
        raise HTTPException(404, "Task not found.")
    if outcome.status == TaskStatus.COMPLETED:
        return {"status": "completed", "result": outcome.result}
    if outcome.status == TaskStatus.FAILED:
        return {"status": "error", "message": outcome.error_message}
    return {"status": "pending", "message": "Task is still in progress."}
