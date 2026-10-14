"""The sweeper's health check: whether its last successful pass is recent.

Each successful pass touches a heartbeat file; the check compares the file's modification
time with the current time. Run as `python -m sweeper.health`, which exits non-zero when
unhealthy.
"""

import sys
import time
from datetime import timedelta
from pathlib import Path

from sweeper.settings import SWEEPER_HEALTHY_WITHIN, SWEEPER_HEARTBEAT_FILE


def record_pass(heartbeat: Path) -> None:
    heartbeat.touch()


def last_pass_is_recent(heartbeat: Path, max_age: timedelta) -> bool:
    try:
        last_pass = heartbeat.stat().st_mtime
    except FileNotFoundError:
        return False
    return time.time() - last_pass <= max_age.total_seconds()


if __name__ == "__main__":
    sys.exit(0 if last_pass_is_recent(SWEEPER_HEARTBEAT_FILE, SWEEPER_HEALTHY_WITHIN) else 1)
