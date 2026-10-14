"""Dependencies that hand the app's long-lived resources to the routes.

The resources are opened and closed by the app's lifespan (`api.main`), which yields them
as lifespan state; Starlette gives each request a copy as `request.state`. These functions
only look them up, so tests can replace them through `app.dependency_overrides`.
"""

from typing import Annotated, TypedDict, cast

from fastapi import Depends, Request

from api.domain.abstractions import TaskStore
from common.data.publisher import TaskPublisher


class AppState(TypedDict):
    task_store: TaskStore
    publisher: TaskPublisher


def get_task_store(request: Request) -> TaskStore:
    return cast(TaskStore, request.state.task_store)


def get_publisher(request: Request) -> TaskPublisher:
    return cast(TaskPublisher, request.state.publisher)


# Shorthands for declaring a dependency in a route's signature, e.g. `task_store: TaskStoreDep`.
TaskStoreDep = Annotated[TaskStore, Depends(get_task_store)]
PublisherDep = Annotated[TaskPublisher, Depends(get_publisher)]
