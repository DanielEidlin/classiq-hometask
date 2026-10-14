"""Routes for submitting tasks and reading their outcomes."""

import uuid
from typing import Annotated, assert_never

import structlog
from fastapi import APIRouter, Depends, HTTPException

from api.domain.outcomes import CompletedOutcome, FailedOutcome, PendingOutcome
from api.presentation.dependencies import PublisherDep, TaskStoreDep
from api.presentation.schemas import (
    CompletedTaskResponse,
    ErrorResponse,
    FailedTaskResponse,
    PendingTaskResponse,
    SubmitTaskRequest,
    SubmitTaskResponse,
)
from api.settings import MAX_CIRCUIT_BYTES, RETRY_AFTER_SECONDS
from common.data.publisher import PublishFailed

log = structlog.get_logger()

router = APIRouter(prefix="/tasks", tags=["tasks"])

SERVICE_BUSY_MESSAGE = "The service is busy. Submit the task again later."
CIRCUIT_TOO_LARGE_MESSAGE = f"Circuit is larger than the limit of {MAX_CIRCUIT_BYTES} bytes."


async def validate_task(body: SubmitTaskRequest) -> SubmitTaskRequest:
    if len(body.quantum_circuit.encode("utf-8")) > MAX_CIRCUIT_BYTES:
        raise HTTPException(413, CIRCUIT_TOO_LARGE_MESSAGE)
    return body


@router.post(
    "",
    status_code=202,
    responses={
        400: {"model": ErrorResponse, "description": "Malformed body"},
        413: {"model": ErrorResponse, "description": "Circuit larger than MAX_CIRCUIT_BYTES"},
        503: {
            "model": ErrorResponse,
            "description": "Queue full, or the broker or database unavailable",
        },
    },
)
async def submit_task(
    body: Annotated[SubmitTaskRequest, Depends(validate_task)],
    task_store: TaskStoreDep,
    publisher: PublisherDep,
) -> SubmitTaskResponse:
    task_id = uuid.uuid4()
    await task_store.insert_task(task_id, body.quantum_circuit)
    try:
        await publisher.publish(task_id)
    except PublishFailed:
        log.warning("task_shed", task_id=str(task_id), exc_info=True)
        await task_store.delete_task(task_id)
        raise HTTPException(
            503,
            SERVICE_BUSY_MESSAGE,
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        ) from None
    await task_store.mark_published(task_id)
    log.info("task_accepted", task_id=str(task_id))
    return SubmitTaskResponse(task_id=task_id)


@router.get(
    "/{task_id}",
    responses={
        404: {"model": ErrorResponse, "description": "Task not found"},
        503: {"model": ErrorResponse, "description": "Database unavailable"},
    },
)
async def get_task(
    task_id: str, task_store: TaskStoreDep
) -> PendingTaskResponse | CompletedTaskResponse | FailedTaskResponse:
    try:
        parsed_id = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(404, "Task not found.") from None

    outcome = await task_store.fetch_outcome(parsed_id)
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
