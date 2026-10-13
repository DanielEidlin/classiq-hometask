"""Starts the full Docker Compose stack for the integration tests and stops it afterwards."""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_NAME = "classiq-hometask-test"
API_PORT = os.environ.get("TEST_API_PORT", "18000")


def _compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "--project-name", PROJECT_NAME, *args],
        cwd=REPO_ROOT,
        env={**os.environ, "API_PORT": API_PORT},
        check=True,
    )


@pytest.fixture(scope="session")
def stack() -> Iterator[None]:
    """The running stack. `up --wait` returns once every health check passes."""
    try:
        _compose("up", "--build", "--detach", "--wait", "--wait-timeout", "300")
        yield
    finally:
        _compose("down", "--volumes", "--remove-orphans")


@pytest.fixture
def api(stack: None) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=f"http://localhost:{API_PORT}", timeout=10) as client:
        yield client
