"""The worker's database engine."""

import sqlalchemy as sa

# What an unreachable database surfaces as: a refused or timed-out connection or statement
# (OperationalError), a connection that died mid-transaction (InterfaceError), and no free
# connection in the pool in time (TimeoutError).
DATABASE_UNAVAILABLE_ERRORS = (sa.exc.OperationalError, sa.exc.InterfaceError, sa.exc.TimeoutError)


def create_db_engine(database_url: str, timeout_seconds: float) -> sa.Engine:
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
