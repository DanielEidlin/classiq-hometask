"""Routes for submitting tasks and reading their outcomes."""

import uuid
from typing import Annotated, assert_never

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncConnection

from api.dependencies import DbConnectionDep, PublisherDep
from api.models import (
    CompletedOutcome,
    CompletedTaskResponse,
    FailedOutcome,
    FailedTaskResponse,
    PendingOutcome,
    PendingTaskResponse,
    SubmitTaskRequest,
    SubmitTaskResponse,
    TaskOutcome,
)
from api.settings import MAX_CIRCUIT_BYTES, RETRY_AFTER_SECONDS
from common.publisher import PublishFailed
from common.tasks import tasks

log = structlog.get_logger()

router = APIRouter(prefix="/tasks", tags=["tasks"])

SERVICE_BUSY_MESSAGE = "The service is busy. Submit the task again later."
CIRCUIT_TOO_LARGE_MESSAGE = f"Circuit is larger than the limit of {MAX_CIRCUIT_BYTES} bytes."

_task_outcome = TypeAdapter[TaskOutcome](TaskOutcome)


async def _fetch_outcome(connection: AsyncConnection, task_id: uuid.UUID) -> TaskOutcome | None:
    query = sa.select(tasks.c.status, tasks.c.result, tasks.c.error_message).where(
        tasks.c.id == task_id
    )
    row = (await connection.execute(query)).one_or_none()
    return None if row is None else _task_outcome.validate_python(dict(row._mapping))


async def _delete_task(connection: AsyncConnection, task_id: uuid.UUID) -> None:
    async with connection.begin():
        await connection.execute(tasks.delete().where(tasks.c.id == task_id))


async def validate_task(body: SubmitTaskRequest) -> SubmitTaskRequest:
    if len(body.quantum_circuit.encode("utf-8")) > MAX_CIRCUIT_BYTES:
        raise HTTPException(413, CIRCUIT_TOO_LARGE_MESSAGE)
    return body


@router.post("", status_code=202)
async def submit_task(
    body: Annotated[SubmitTaskRequest, Depends(validate_task)],
    connection: DbConnectionDep,
    publisher: PublisherDep,
) -> SubmitTaskResponse:
    task_id = uuid.uuid4()
    async with connection.begin():
        await connection.execute(tasks.insert().values(id=task_id, circuit=body.quantum_circuit))
    try:
        await publisher.publish(task_id)
    except PublishFailed:
        log.warning("task_shed", task_id=str(task_id), exc_info=True)
        await _delete_task(connection, task_id)
        raise HTTPException(
            503,
            SERVICE_BUSY_MESSAGE,
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        ) from None
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
