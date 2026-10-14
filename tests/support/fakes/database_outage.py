"""A database outage, simulated on a real engine."""

import sqlalchemy as sa

CONNECTION_REFUSED = sa.exc.OperationalError("", {}, ConnectionRefusedError("connection refused"))


class DatabaseOutage:
    """Fails every statement on an engine with `error` while down, as an unreachable
    database would by default."""

    def __init__(self, engine: sa.Engine, error: Exception = CONNECTION_REFUSED) -> None:
        self.down = False
        self.recover_after_failures: int | None = None
        self._error = error
        self._failures = 0
        sa.event.listen(engine, "before_cursor_execute", self._fail_while_down)

    def _fail_while_down(self, *args: object) -> None:
        if not self.down:
            return
        self._failures += 1
        if self._failures == self.recover_after_failures:
            self.down = False
        raise self._error
