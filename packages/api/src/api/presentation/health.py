"""The health check: whether the API can reach Postgres and RabbitMQ."""

import structlog
from fastapi import APIRouter, HTTPException

from api.presentation.dependencies import PublisherDep, TaskStoreDep
from api.presentation.schemas import ErrorResponse, HealthResponse

log = structlog.get_logger()

router = APIRouter(tags=["health"])


@router.get("/health", responses={503: {"model": ErrorResponse}})
async def health(task_store: TaskStoreDep, publisher: PublisherDep) -> HealthResponse:
    if not await task_store.is_reachable():
        log.warning("health_check_failed", dependency="postgres")
        raise HTTPException(503, "Postgres is unreachable.")
    if not publisher.is_connected:
        log.warning("health_check_failed", dependency="rabbitmq")
        raise HTTPException(503, "RabbitMQ is unreachable.")
    return HealthResponse()
