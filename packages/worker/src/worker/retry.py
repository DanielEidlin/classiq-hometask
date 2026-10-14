"""How long to wait between attempts at a write the database couldn't take."""

from collections.abc import Iterator

from pydantic import BaseModel


class RetryPolicy(BaseModel, frozen=True):
    first_delay_seconds: float
    max_delay_seconds: float
    give_up_seconds: float


def backoff_delays(first_delay_seconds: float, max_delay_seconds: float) -> Iterator[float]:
    """An endless schedule: each delay doubles the last, up to `max_delay_seconds`."""
    delay = first_delay_seconds
    while True:
        yield min(delay, max_delay_seconds)
        delay *= 2
