"""The worker's simulation limits, read from the environment."""

import os

from worker.runner import SimulationLimits

SIMULATION_LIMITS = SimulationLimits(
    shots=int(os.environ.get("SHOTS", "1024")),
    max_memory_mb=int(os.environ.get("AER_MAX_MEMORY_MB", "1700")),
    timeout_seconds=float(os.environ.get("TASK_TIMEOUT_SECONDS", "60")),
)
