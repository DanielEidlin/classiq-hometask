"""Controls the Docker Compose stack the integration tests run against."""

import json
import os
import subprocess
from pathlib import Path

from common.task_queue import TASK_QUEUE

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_NAME = "classiq-hometask-test"
# Shortens limits so slow cases (a timeout) run quickly.
TEST_OVERRIDE_FILE = "compose.test.yaml"
API_PORT = os.environ.get("TEST_API_PORT", "18000")
POSTGRES_PORT = os.environ.get("TEST_POSTGRES_PORT", "15432")
# The credentials compose.yaml gives Postgres, through the port compose.test.yaml publishes.
DATABASE_URL = f"postgresql+psycopg://tasks:tasks@localhost:{POSTGRES_PORT}/tasks"
WAIT_TIMEOUT_SECONDS = "300"
TASK_QUEUE_CAP_POLICY = "test-task-queue-cap"

_COMPOSE_ENVIRONMENT = {**os.environ, "API_PORT": API_PORT, "POSTGRES_PORT": POSTGRES_PORT}


def compose(*args: str) -> None:
    subprocess.run(
        [
            "docker", "compose", "--project-name", PROJECT_NAME,
            "--file", "compose.yaml", "--file", TEST_OVERRIDE_FILE,
            *args,
        ],
        cwd=REPO_ROOT,
        env=_COMPOSE_ENVIRONMENT,
        check=True,
    )


def rabbitmqctl(*args: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "--project-name", PROJECT_NAME, "exec", "-T", "rabbitmq", "rabbitmqctl", *args],
        cwd=REPO_ROOT,
        env=_COMPOSE_ENVIRONMENT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


class ComposeStack:
    """Stops and starts services of the running Compose stack, for tests that need to hold work back."""

    def stop(self, service: str) -> None:
        compose("stop", service)

    def start(self, service: str) -> None:
        compose("start", service)

    def kill(self, service: str) -> None:
        """Kill every replica of the service with SIGKILL, as a crash would, with no graceful shutdown."""
        compose("kill", service)

    def execute_sql(self, statement: str) -> None:
        compose(
            "exec", "-T", "postgres",
            "psql", "--username", "tasks", "--dbname", "tasks", "-v", "ON_ERROR_STOP=1",
            "--command", statement,
        )

    def restart(self, service: str) -> None:
        # `restart` returns once the container is running, not once it is healthy.
        compose("restart", service)
        compose("up", "--detach", "--wait", "--wait-timeout", WAIT_TIMEOUT_SECONDS, "--no-deps", service)

    def purge_task_queue(self) -> None:
        rabbitmqctl("purge_queue", TASK_QUEUE)

    def task_queue_arguments(self) -> dict[str, object]:
        """The arguments the task queue was declared with, plus any RabbitMQ adds itself."""
        queues = json.loads(rabbitmqctl("list_queues", "--quiet", "--formatter", "json", "name", "arguments"))
        # The JSON formatter renders each argument as a [name, AMQP type, value] triple.
        triples = next(queue["arguments"] for queue in queues if queue["name"] == TASK_QUEUE)
        return {name: value for name, _amqp_type, value in triples}

    def cap_task_queue(self, max_length: int) -> None:
        """Lower the task queue's cap with a policy: where a policy and the queue's own
        `x-max-length` both set one, RabbitMQ applies the lower."""
        rabbitmqctl(
            "set_policy", "--apply-to", "queues", TASK_QUEUE_CAP_POLICY,
            f"^{TASK_QUEUE}$", json.dumps({"max-length": max_length}),
        )

    def remove_task_queue_cap(self) -> None:
        rabbitmqctl("clear_policy", TASK_QUEUE_CAP_POLICY)
