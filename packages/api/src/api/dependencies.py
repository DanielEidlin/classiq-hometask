"""Dependencies that hand the app's long-lived resources to the routes.

The resources are opened and closed by the app's lifespan (`api.main`); these functions
only look them up, so tests can replace them through `app.dependency_overrides`.
"""

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from common.publisher import TaskPublisher


def get_engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.engine)


def get_publisher(request: Request) -> TaskPublisher:
    return cast(TaskPublisher, request.app.state.publisher)


async def get_db_connection(
    engine: Annotated[AsyncEngine, Depends(get_engine)],
) -> AsyncIterator[AsyncConnection]:
    """A database connection for the duration of the request."""
    async with engine.connect() as connection:
        yield connection


# Shorthands for declaring a dependency in a route's signature, e.g. `connection: DbConnectionDep`.
EngineDep = Annotated[AsyncEngine, Depends(get_engine)]
PublisherDep = Annotated[TaskPublisher, Depends(get_publisher)]
DbConnectionDep = Annotated[AsyncConnection, Depends(get_db_connection)]
