"""The heartbeat file: each successful pass touches it, and its modification time says when the
last one happened."""

import time
from datetime import timedelta
from pathlib import Path


def record_pass(heartbeat: Path) -> None:
    heartbeat.touch()


def last_pass_is_recent(heartbeat: Path, max_age: timedelta) -> bool:
    try:
        last_pass = heartbeat.stat().st_mtime
    except FileNotFoundError:
        return False
    return time.time() - last_pass <= max_age.total_seconds()
