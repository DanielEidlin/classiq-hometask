"""What an unreachable database looks like, for the services that react to an outage."""

import sqlalchemy as sa

# What an unreachable database surfaces as: a refused or timed-out connection or statement
# (OperationalError), a connection that died mid-transaction (InterfaceError), and no free
# connection in the pool in time (TimeoutError).
DATABASE_UNAVAILABLE_ERRORS = (sa.exc.OperationalError, sa.exc.InterfaceError, sa.exc.TimeoutError)
