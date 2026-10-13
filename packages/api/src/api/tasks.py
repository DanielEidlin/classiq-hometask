"""Routes for submitting tasks and reading their outcomes."""

import os
import uuid
from typing import Annotated, Literal, assert_never

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncConnection

from api.dependencies import DbConnectionDep, PublisherDep
from common.tasks import TaskStatus, tasks

log = structlog.get_logger()

router = APIRouter(prefix="/tasks", tags=["tasks"])

MAX_CIRCUIT_BYTES = int(os.environ.get("MAX_CIRCUIT_BYTES", "65536"))


class SubmitTaskRequest(BaseModel):
    quantum_circuit: str = Field(
        alias="qc", min_length=1, description="The circuit, as QASM3 text."
    )


class SubmitTaskResponse(BaseModel):
    task_id: uuid.UUID
    message: Literal["Task submitted successfully."] = "Task submitted successfully."


class PendingTaskResponse(BaseModel):
    status: Literal["pending"] = "pending"
    message: Literal["Task is still in progress."] = "Task is still in progress."


class CompletedTaskResponse(BaseModel):
    status: Literal["completed"] = "completed"
    result: dict[str, int]


class FailedTaskResponse(BaseModel):
    status: Literal["error"] = "error"
    message: str


class PendingOutcome(BaseModel):
    status: Literal[TaskStatus.PENDING]


class CompletedOutcome(BaseModel):
    status: Literal[TaskStatus.COMPLETED]
    result: dict[str, int]


class FailedOutcome(BaseModel):
    status: Literal[TaskStatus.FAILED]
    error_message: str


# The stored state of a task. Validation picks the model by status, so a completed task
# without counts, or a failed one without a reason, is rejected here rather than served.
TaskOutcome = Annotated[
    PendingOutcome | CompletedOutcome | FailedOutcome, Field(discriminator="status")
]
_task_outcome = TypeAdapter[TaskOutcome](TaskOutcome)


async def _fetch_outcome(connection: AsyncConnection, task_id: uuid.UUID) -> TaskOutcome | None:
    query = sa.select(tasks.c.status, tasks.c.result, tasks.c.error_message).where(
        tasks.c.id == task_id
    )
    row = (await connection.execute(query)).one_or_none()
    return None if row is None else _task_outcome.validate_python(dict(row._mapping))


@router.post("", status_code=202)
async def submit_task(
    body: SubmitTaskRequest, connection: DbConnectionDep, publisher: PublisherDep
) -> SubmitTaskResponse:
    if len(body.quantum_circuit.encode("utf-8")) > MAX_CIRCUIT_BYTES:
        raise HTTPException(413, f"Circuit is larger than the limit of {MAX_CIRCUIT_BYTES} bytes.")
    task_id = uuid.uuid4()
    async with connection.begin():
        await connection.execute(tasks.insert().values(id=task_id, circuit=body.quantum_circuit))
    await publisher.publish(task_id)
    async with connection.begin():
        await connection.execute(tasks.update().where(tasks.c.id == task_id).values(published=True))
    log.info("task_accepted", task_id=str(task_id))
    return SubmitTaskResponse(task_id=task_id)


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
    match outcome:
        case PendingOutcome():
            return PendingTaskResponse()
        case CompletedOutcome(result=counts):
            return CompletedTaskResponse(result=counts)
        case FailedOutcome(error_message=reason):
            return FailedTaskResponse(message=reason)
        case _:
            assert_never(outcome)
