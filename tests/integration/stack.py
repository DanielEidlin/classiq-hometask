"""Controls the Docker Compose stack the integration tests run against."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_NAME = "classiq-hometask-test"
API_PORT = os.environ.get("TEST_API_PORT", "18000")


def compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "--project-name", PROJECT_NAME, *args],
        cwd=REPO_ROOT,
        env={**os.environ, "API_PORT": API_PORT},
        check=True,
    )


class Stack:
    """Controls services of the running stack, for tests that need to hold work back."""

    def stop(self, service: str) -> None:
        compose("stop", service)

    def start(self, service: str) -> None:
        compose("start", service)
