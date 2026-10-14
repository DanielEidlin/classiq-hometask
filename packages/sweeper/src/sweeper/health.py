"""The sweeper's health check: whether its last successful pass is recent.

Run as `python -m sweeper.health`, which exits non-zero when unhealthy.
"""

import sys

from sweeper.data.heartbeat import last_pass_is_recent
from sweeper.settings import SWEEPER_HEALTHY_WITHIN, SWEEPER_HEARTBEAT_FILE

if __name__ == "__main__":
    sys.exit(0 if last_pass_is_recent(SWEEPER_HEARTBEAT_FILE, SWEEPER_HEALTHY_WITHIN) else 1)
