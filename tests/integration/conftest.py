"""Starts the full Docker Compose stack for the integration tests and stops it afterwards."""

from collections.abc import Iterator

import httpx
import pytest

from stack import API_PORT, WAIT_TIMEOUT_SECONDS, ComposeStack, compose


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run the resilience tests last, so a stack they fail to restore can't break the others."""
    items.sort(key=lambda item: item.get_closest_marker("resilience") is not None)


@pytest.fixture(scope="session")
def compose_stack() -> Iterator[ComposeStack]:
    """The running stack. `up --wait` returns once every health check passes."""
    try:
        compose("up", "--build", "--detach", "--wait", "--wait-timeout", WAIT_TIMEOUT_SECONDS)
        yield ComposeStack()
    finally:
        compose("down", "--volumes", "--remove-orphans")


@pytest.fixture(scope="session")
def api(compose_stack: ComposeStack) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=f"http://localhost:{API_PORT}", timeout=10) as client:
        yield client
