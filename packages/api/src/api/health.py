"""The health check: whether the API can reach Postgres and RabbitMQ."""

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, HTTPException

from api.dependencies import EngineDep, PublisherDep

log = structlog.get_logger()

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(engine: EngineDep, publisher: PublisherDep) -> dict[str, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.text("SELECT 1"))
    except Exception:
        log.warning("health_check_failed", dependency="postgres", exc_info=True)
        raise HTTPException(503, "Postgres is unreachable.") from None
    if not publisher.is_connected:
        log.warning("health_check_failed", dependency="rabbitmq")
        raise HTTPException(503, "RabbitMQ is unreachable.")
    return {"status": "ok"}
