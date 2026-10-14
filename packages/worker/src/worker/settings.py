"""The worker's simulation limits, attempt cap and database timings, read from the environment."""

import os

from worker.retry import RetryPolicy
from worker.runner import SimulationLimits

SIMULATION_LIMITS = SimulationLimits(
    shots=int(os.environ.get("SHOTS", "1024")),
    max_memory_mb=int(os.environ.get("AER_MAX_MEMORY_MB", "1700")),
    timeout_seconds=float(os.environ.get("TASK_TIMEOUT_SECONDS", "60")),
)

# Deliveries of a task, each ended by a lost worker, before it is failed with WORKER_LOST.
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))

# Every database call the worker makes fails after this long instead of hanging.
DB_TIMEOUT_SECONDS = float(os.environ.get("DB_TIMEOUT_SECONDS", "10"))

# Retrying a database call while the database is unreachable. Giving up should come before
# RabbitMQ's consumer_timeout, which counts from delivery and so includes the simulation.
DB_RETRY_POLICY = RetryPolicy(
    first_delay_seconds=float(os.environ.get("DB_RETRY_FIRST_DELAY_SECONDS", "1")),
    max_delay_seconds=float(os.environ.get("DB_RETRY_MAX_DELAY_SECONDS", "30")),
    give_up_seconds=float(os.environ.get("DB_OUTAGE_GIVE_UP_SECONDS", "240")),
)
