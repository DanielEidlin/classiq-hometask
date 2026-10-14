import itertools

from worker.retry import backoff_delays

FIRST_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
# Enough delays to show the doubling, the cap, and the delays after it.
DELAYS_SHOWN = 8


def test_backoff_doubles_until_it_reaches_the_cap_and_then_stays_there() -> None:
    delays = list(itertools.islice(backoff_delays(FIRST_DELAY_SECONDS, MAX_DELAY_SECONDS), DELAYS_SHOWN))

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]
