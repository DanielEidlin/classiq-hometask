"""Routes for submitting tasks and reading their outcomes."""

import uuid
from typing import Literal, assert_never

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from api.dependencies import DbConnectionDep, PublisherDep
from common.tasks import TaskStatus, tasks

log = structlog.get_logger()

router = APIRouter(prefix="/tasks", tags=["tasks"])


class SubmitTaskRequest(BaseModel):
    quantum_circuit: str = Field(alias="qc", description="The circuit, as QASM3 text.")


class SubmitTaskResponse(BaseModel):
    task_id: uuid.UUID
    message: str


class PendingTaskResponse(BaseModel):
    status: Literal["pending"] = "pending"
    message: str = "Task is still in progress."


class CompletedTaskResponse(BaseModel):
    status: Literal["completed"] = "completed"
    result: dict[str, int]


class FailedTaskResponse(BaseModel):
    status: Literal["error"] = "error"
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
    body: SubmitTaskRequest, connection: DbConnectionDep, publisher: PublisherDep
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
async def get_task(
    task_id: str, connection: DbConnectionDep
) -> PendingTaskResponse | CompletedTaskResponse | FailedTaskResponse:
    try:
        parsed_id = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(404, "Task not found.") from None

    outcome = await _fetch_outcome(connection, parsed_id)
    if outcome is None:
        raise HTTPException(404, "Task not found.")
    match outcome.status:
        case TaskStatus.PENDING:
            return PendingTaskResponse()
        case TaskStatus.COMPLETED:
            assert outcome.result is not None, "a completed task always has counts"
            return CompletedTaskResponse(result=outcome.result)
        case TaskStatus.FAILED:
            assert outcome.error_message is not None, "a failed task always has a reason"
            return FailedTaskResponse(message=outcome.error_message)
        case _:
            assert_never(outcome.status)
