"""The health check: whether the API can reach Postgres and RabbitMQ."""

import structlog
from fastapi import APIRouter, HTTPException

from api.dependencies import PublisherDep, TaskStoreDep

log = structlog.get_logger()

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(task_store: TaskStoreDep, publisher: PublisherDep) -> dict[str, str]:
    if not await task_store.is_reachable():
        raise HTTPException(503, "Postgres is unreachable.")
    if not publisher.is_connected:
        log.warning("health_check_failed", dependency="rabbitmq")
        raise HTTPException(503, "RabbitMQ is unreachable.")
    return {"status": "ok"}
