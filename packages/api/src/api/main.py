"""The HTTP API: accepts tasks and reports their outcomes."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.exceptions import HTTPException

from api import health, tasks
from common.log import configure_logging
from common.publisher import TaskPublisher

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database engine and the broker connection for the app's lifetime."""
    configure_logging("api")
    app.state.engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    app.state.publisher = await TaskPublisher.connect(os.environ["RABBITMQ_URL"])
    yield
    await app.state.publisher.close()
    await app.state.engine.dispose()


app = FastAPI(title="Quantum Circuit Execution", lifespan=lifespan)
app.include_router(tasks.router)
app.include_router(health.router)


@app.exception_handler(HTTPException)
async def render_error(request: Request, exc: HTTPException) -> JSONResponse:
    """Render every HTTP error in the API's single error shape."""
    log.info(
        "request_failed",
        method=request.method,
        path=request.url.path,
        status_code=exc.status_code,
        message=exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "message": exc.detail},
        headers=exc.headers,
    )
