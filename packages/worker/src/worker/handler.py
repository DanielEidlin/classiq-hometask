"""The task handler: starts an attempt at a pending task, simulates its circuit and records
the outcome.

It has no Celery or database dependency; `worker.tasks` builds it and binds it to the queue.

A task may be delivered more than once: Celery acknowledges a message only after the task
finishes, so RabbitMQ redelivers it when a worker dies. The store records only the first
outcome, so a later one is discarded here.

`DatabaseUnavailable` from the store propagates, for the message to be returned to the queue.
A requeue after the attempt was counted has used it up, so an outage that outlasts the
store's retries MAX_ATTEMPTS times in a row fails the task as WORKER_LOST.
"""

import uuid
from typing import assert_never

import structlog

from common.tasks import ErrorCode
from worker.abstractions import CircuitRunner, TaskStore
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome

log = structlog.get_logger()

WORKER_LOST_MESSAGE = (
    "The worker running the task was lost {max_attempts} times, so it was not run again."
)


def _log_recorded(task_log: structlog.typing.FilteringBoundLogger, outcome: SimulationOutcome) -> None:
    match outcome:
        case SimulationCompleted():
            task_log.info("task_completed")
        case SimulationFailed(code=code, message=message, traceback=traceback):
            # A traceback means something raised, and workers lost on every attempt suggest a
            # circuit that crashes them: faults to investigate, not just a bad circuit. The
            # traceback crossed the process boundary as text, so it's logged as a field.
            if traceback is not None:
                log_failure = task_log.error
            elif code == ErrorCode.WORKER_LOST:
                log_failure = task_log.warning
            else:
                log_failure = task_log.info
            log_failure("task_failed", error_code=code, message=message, traceback=traceback)
        case _:
            assert_never(outcome)


class TaskHandler:
    def __init__(self, store: TaskStore, circuit_runner: CircuitRunner, max_attempts: int) -> None:
        self._store = store
        self._circuit_runner = circuit_runner
        self._max_attempts = max_attempts

    def handle(self, task_id: uuid.UUID) -> None:
        task_log = log.bind(task_id=str(task_id))

        started = self._store.start_attempt(task_id)
        if started is None:
            # A separate read: starting an attempt can't tell a final task from a missing one.
            status = self._store.fetch_status(task_id)
            if status is None:
                task_log.info("task_skipped", reason="not found")
            else:
                task_log.info("task_skipped", reason="already final", status=status)
            return
        task_log.info("task_started", attempt=started.number)

        outcome: SimulationOutcome
        if started.number > self._max_attempts:
            outcome = SimulationFailed(
                code=ErrorCode.WORKER_LOST,
                message=WORKER_LOST_MESSAGE.format(max_attempts=self._max_attempts),
            )
        else:
            outcome = self._circuit_runner.run(started.circuit)

        if self._store.record_outcome(task_id, outcome):
            _log_recorded(task_log, outcome)
        else:
            task_log.info("duplicate_result_discarded")
