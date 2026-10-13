import os
import time
from datetime import timedelta
from pathlib import Path

from sweeper.health import last_pass_is_recent, record_pass

MAX_AGE = timedelta(seconds=60)


def test_healthy_right_after_a_pass(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"

    record_pass(heartbeat)

    assert last_pass_is_recent(heartbeat, MAX_AGE)


def test_unhealthy_when_the_last_pass_is_too_old(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    record_pass(heartbeat)
    too_old = time.time() - (MAX_AGE * 2).total_seconds()
    os.utime(heartbeat, (too_old, too_old))

    assert not last_pass_is_recent(heartbeat, MAX_AGE)


def test_unhealthy_before_the_first_pass(tmp_path: Path) -> None:
    assert not last_pass_is_recent(tmp_path / "heartbeat", MAX_AGE)


def test_a_later_pass_makes_it_healthy_again(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    record_pass(heartbeat)
    too_old = time.time() - (MAX_AGE * 2).total_seconds()
    os.utime(heartbeat, (too_old, too_old))

    record_pass(heartbeat)

    assert last_pass_is_recent(heartbeat, MAX_AGE)
