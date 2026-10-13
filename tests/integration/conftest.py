"""Starts the full Docker Compose stack for the integration tests and stops it afterwards."""

from collections.abc import Iterator

import httpx
import pytest

from stack import API_PORT, Stack, compose


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    """The running stack. `up --wait` returns once every health check passes."""
    try:
        compose("up", "--build", "--detach", "--wait", "--wait-timeout", "300")
        yield Stack()
    finally:
        compose("down", "--volumes", "--remove-orphans")


@pytest.fixture
def api(stack: Stack) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=f"http://localhost:{API_PORT}", timeout=10) as client:
        yield client
