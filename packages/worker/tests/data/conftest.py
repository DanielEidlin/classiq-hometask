import pytest
import tenacity

from worker.data.database import retry_while_unavailable

# The production defaults (`worker/settings.py`), so the delays the tests expect are the real ones.
FIRST_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
GIVE_UP_SECONDS = 240.0


@pytest.fixture
def retrying() -> tenacity.Retrying:
    return retry_while_unavailable(FIRST_DELAY_SECONDS, MAX_DELAY_SECONDS, GIVE_UP_SECONDS)
