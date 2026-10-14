"""Controls the Docker Compose stack the integration tests run against."""

import os
import subprocess
from pathlib import Path

from common.task_queue import TASK_QUEUE

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_NAME = "classiq-hometask-test"
API_PORT = os.environ.get("TEST_API_PORT", "18000")
WAIT_TIMEOUT_SECONDS = "300"
# Small, so the backpressure test fills the queue quickly, but above the most any other test
# queues at once (test_rabbitmq_restart: 6), since the cap applies to the whole test stack.
MAX_QUEUED_TASKS = 10


def compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "--project-name", PROJECT_NAME, *args],
        cwd=REPO_ROOT,
        env={**os.environ, "API_PORT": API_PORT, "MAX_QUEUED_TASKS": str(MAX_QUEUED_TASKS)},
        check=True,
    )


class ComposeStack:
    """Stops and starts services of the running Compose stack, for tests that need to hold work back."""

    def stop(self, service: str) -> None:
        compose("stop", service)

    def start(self, service: str) -> None:
        compose("start", service)

    def restart(self, service: str) -> None:
        # `restart` returns once the container is running, not once it is healthy.
        compose("restart", service)
        compose("up", "--detach", "--wait", "--wait-timeout", WAIT_TIMEOUT_SECONDS, "--no-deps", service)

    def purge_task_queue(self) -> None:
        compose("exec", "rabbitmq", "rabbitmqctl", "purge_queue", TASK_QUEUE)
