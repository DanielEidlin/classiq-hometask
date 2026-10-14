"""Configuration shared by the services, read from the environment."""

import os

MAX_QUEUED_TASKS = int(os.environ.get("MAX_QUEUED_TASKS", "10000"))
PUBLISH_TIMEOUT_SECONDS = float(os.environ.get("PUBLISH_TIMEOUT_SECONDS", "5"))
