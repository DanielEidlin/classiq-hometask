"""The HTTP API: accepts tasks and reports their outcomes."""

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from common.log import configure_logging
from common.publisher import TaskPublisher
from common.tasks import TaskStatus, tasks

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("api")
    app.state.engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    app.state.publisher = TaskPublisher(os.environ["RABBITMQ_URL"])
    await app.state.publisher.connect()
    yield
    await app.state.publisher.close()
    await app.state.engine.dispose()


app = FastAPI(title="Quantum Circuit Execution", lifespan=lifespan)


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def _publisher(request: Request) -> TaskPublisher:
    publisher: TaskPublisher = request.app.state.publisher
    return publisher


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": "error", "message": message})


class SubmitTaskRequest(BaseModel):
    qc: str


class SubmitTaskResponse(BaseModel):
    task_id: uuid.UUID
    message: str


@app.post("/tasks", status_code=202)
async def submit_task(body: SubmitTaskRequest, request: Request) -> SubmitTaskResponse:
    task_id = uuid.uuid4()
    engine = _engine(request)
    async with engine.begin() as conn:
        await conn.execute(tasks.insert().values(id=task_id, qc=body.qc))
    await _publisher(request).publish(task_id)
    async with engine.begin() as conn:
        await conn.execute(tasks.update().where(tasks.c.id == task_id).values(published=True))
    log.info("task_accepted", task_id=str(task_id))
    return SubmitTaskResponse(task_id=task_id, message="Task submitted successfully.")


@app.get("/tasks/{task_id}", response_model=None)
async def get_task(task_id: str, request: Request) -> dict[str, Any] | JSONResponse:
    try:
        parsed_id = uuid.UUID(task_id)
    except ValueError:
        return _error(404, "Task not found.")

    query = sa.select(tasks.c.status, tasks.c.result, tasks.c.error_message).where(
        tasks.c.id == parsed_id
    )
    async with _engine(request).connect() as conn:
        row = (await conn.execute(query)).one_or_none()

    if row is None:
        return _error(404, "Task not found.")
    if row.status == TaskStatus.COMPLETED:
        return {"status": "completed", "result": row.result}
    if row.status == TaskStatus.FAILED:
        return {"status": "error", "message": row.error_message}
    return {"status": "pending", "message": "Task is still in progress."}


@app.get("/health", response_model=None)
async def health(request: Request) -> dict[str, str] | JSONResponse:
    try:
        async with _engine(request).connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    except Exception:
        log.warning("health_check_failed", dependency="postgres", exc_info=True)
        return _error(503, "Postgres is unreachable.")
    if not _publisher(request).is_connected:
        log.warning("health_check_failed", dependency="rabbitmq")
        return _error(503, "RabbitMQ is unreachable.")
    return {"status": "ok"}
