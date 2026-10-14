"""The worker's database engine, and how its calls are retried while the database is down."""

import sqlalchemy as sa
import tenacity

# What an unreachable database surfaces as: a refused or timed-out connection or statement
# (OperationalError), a connection that died mid-transaction (InterfaceError), and no free
# connection in the pool in time (TimeoutError).
DATABASE_UNAVAILABLE_ERRORS = (sa.exc.OperationalError, sa.exc.InterfaceError, sa.exc.TimeoutError)


def create_database_engine(database_url: str, timeout_seconds: float) -> sa.Engine:
    """An engine whose every database call fails within about `timeout_seconds` rather than hang."""
    timeout_ms = int(timeout_seconds * 1000)
    return sa.create_engine(
        database_url,
        pool_pre_ping=True,
        pool_timeout=timeout_seconds,
        connect_args={
            # libpq takes whole seconds, and treats 0 as no timeout.
            "connect_timeout": max(1, round(timeout_seconds)),
            # statement_timeout bounds a slow query; tcp_user_timeout bounds a peer that
            # stopped answering mid-query, which the server-side timeout can't see.
            "options": f"-c statement_timeout={timeout_ms}",
            "tcp_user_timeout": timeout_ms,
        },
    )


def retry_while_unavailable(
    first_delay_seconds: float, max_delay_seconds: float, give_up_seconds: float
) -> tenacity.Retrying:
    """Retries a call while the database is unreachable, waiting `first_delay_seconds` and
    doubling up to `max_delay_seconds`. It gives up rather than sleep past `give_up_seconds`,
    raising `tenacity.RetryError`."""
    return tenacity.Retrying(
        retry=tenacity.retry_if_exception_type(DATABASE_UNAVAILABLE_ERRORS),
        wait=tenacity.wait_exponential(multiplier=first_delay_seconds, max=max_delay_seconds),
        stop=tenacity.stop_before_delay(give_up_seconds),
    )
