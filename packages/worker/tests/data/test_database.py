import pytest
import sqlalchemy as sa
import tenacity

from worker.data.database import create_database_engine, retry_while_unavailable

FIRST_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
GIVE_UP_SECONDS = 240.0
# Enough failed calls to show the doubling, the cap, and the delays after it.
FAILED_CALLS_SHOWN = 8
DB_TIMEOUT_SECONDS = 1
# Longer than DB_TIMEOUT_SECONDS, so the query only returns if nothing times it out.
QUERY_SECONDS = 30


def _retry_state(retrying: tenacity.Retrying, failed_calls: int) -> tenacity.RetryCallState:
    retry_state = tenacity.RetryCallState(retry_object=retrying, fn=None, args=(), kwargs={})
    retry_state.attempt_number = failed_calls
    return retry_state


def _retrying() -> tenacity.Retrying:
    return retry_while_unavailable(FIRST_DELAY_SECONDS, MAX_DELAY_SECONDS, GIVE_UP_SECONDS)


def test_backoff_doubles_until_it_reaches_the_cap_and_then_stays_there() -> None:
    retrying = _retrying()

    delays = [
        retrying.wait(_retry_state(retrying, failed_calls))
        for failed_calls in range(1, FAILED_CALLS_SHOWN + 1)
    ]

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


@pytest.mark.parametrize(
    ("seconds_since_start", "upcoming_sleep", "gives_up"),
    [
        pytest.param(200.0, 30.0, False, id="the next sleep ends before the give-up time"),
        pytest.param(220.0, 30.0, True, id="the next sleep would pass the give-up time"),
    ],
)
def test_retrying_gives_up_rather_than_sleep_past_the_give_up_time(
    seconds_since_start: float, upcoming_sleep: float, gives_up: bool
) -> None:
    retrying = _retrying()
    retry_state = _retry_state(retrying, failed_calls=1)
    retry_state.outcome_timestamp = retry_state.start_time + seconds_since_start
    retry_state.upcoming_sleep = upcoming_sleep

    assert retrying.stop(retry_state) is gives_up


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(sa.exc.OperationalError("", {}, Exception()), id="unreachable"),
        pytest.param(sa.exc.InterfaceError("", {}, Exception()), id="connection closed"),
        pytest.param(sa.exc.TimeoutError(), id="pool exhausted"),
    ],
)
def test_unreachable_database_errors_are_retried(error: Exception) -> None:
    retrying = _retrying()
    retry_state = _retry_state(retrying, failed_calls=1)
    retry_state.set_exception((type(error), error, None))

    assert retrying.retry(retry_state)


def test_other_database_errors_are_not_retried() -> None:
    retrying = _retrying()
    retry_state = _retry_state(retrying, failed_calls=1)
    error = sa.exc.IntegrityError("", {}, Exception())
    retry_state.set_exception((type(error), error, None))

    assert not retrying.retry(retry_state)


def test_database_calls_time_out_instead_of_hanging(db_engine: sa.Engine) -> None:
    engine = create_database_engine(db_engine.url.render_as_string(hide_password=False), DB_TIMEOUT_SECONDS)
    try:
        with pytest.raises(sa.exc.OperationalError), engine.connect() as connection:
            connection.execute(sa.select(sa.func.pg_sleep(QUERY_SECONDS)))
    finally:
        engine.dispose()
