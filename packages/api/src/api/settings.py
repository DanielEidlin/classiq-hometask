"""The API's configuration, read from the environment."""

import os

MAX_CIRCUIT_BYTES = int(os.environ.get("MAX_CIRCUIT_BYTES", "65536"))
RETRY_AFTER_SECONDS = int(os.environ.get("RETRY_AFTER_SECONDS", "5"))
