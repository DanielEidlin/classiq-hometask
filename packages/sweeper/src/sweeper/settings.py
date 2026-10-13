"""The sweeper's configuration, read from the environment."""

import os
from datetime import timedelta
from pathlib import Path

SWEEP_INTERVAL_SECONDS = float(os.environ.get("SWEEP_INTERVAL_SECONDS", "10"))
SWEEP_GRACE = timedelta(seconds=float(os.environ.get("SWEEP_GRACE_SECONDS", "30")))
STALE_TASK_AFTER = timedelta(seconds=float(os.environ.get("STALE_TASK_SECONDS", "600")))
SWEEPER_HEALTHY_WITHIN = timedelta(
    seconds=float(os.environ.get("SWEEPER_HEALTHY_WITHIN_SECONDS", "60"))
)
SWEEPER_HEARTBEAT_FILE = Path(os.environ.get("SWEEPER_HEARTBEAT_FILE", "/tmp/sweeper-heartbeat"))
