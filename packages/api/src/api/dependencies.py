"""Dependencies that hand the app's long-lived resources to the routes.

The resources are opened and closed by the app's lifespan (`api.main`); these functions
only look them up, so tests can replace them through `app.dependency_overrides`.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from common.publisher import TaskPublisher


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def get_publisher(request: Request) -> TaskPublisher:
    publisher: TaskPublisher = request.app.state.publisher
    return publisher


async def get_connection(engine: Annotated[AsyncEngine, Depends(get_engine)]) -> AsyncIterator[AsyncConnection]:
    """A database connection for the duration of the request."""
    async with engine.connect() as connection:
        yield connection


EngineDep = Annotated[AsyncEngine, Depends(get_engine)]
PublisherDep = Annotated[TaskPublisher, Depends(get_publisher)]
ConnectionDep = Annotated[AsyncConnection, Depends(get_connection)]
