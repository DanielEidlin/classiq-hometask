"""The HTTP API: accepts tasks and reports their outcomes."""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.exceptions import HTTPException

from api import health, tasks
from api.dependencies import AppState
from common.log import configure_logging
from common.publisher import TaskPublisher

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[AppState]:
    """Open the database engine and the broker connection for the app's lifetime."""
    configure_logging("api")
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    publisher = await TaskPublisher.connect(os.environ["RABBITMQ_URL"])
    yield {"engine": engine, "publisher": publisher}
    await publisher.close()
    await engine.dispose()


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


@app.exception_handler(RequestValidationError)
async def render_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Answer a malformed request with 400 in the error shape, instead of FastAPI's 422."""
    message = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )
    return await render_error(request, HTTPException(400, message))


@app.middleware("http")
async def render_unexpected_error(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Turn an unhandled exception into a 500 in the error shape, logging its traceback.

    A middleware rather than an `Exception` handler: Starlette re-raises after running such
    a handler, so Uvicorn would log the traceback a second time, as plain text.
    """
    try:
        return await call_next(request)
    except Exception:
        log.exception("request_crashed", method=request.method, path=request.url.path)
        return JSONResponse(
            status_code=500, content={"status": "error", "message": "Internal server error."}
        )
