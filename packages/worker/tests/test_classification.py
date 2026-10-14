from common.tasks import ErrorCode
from worker.outcomes import SimulationCompleted, SimulationFailed
from worker.runner import (
    KILLED_MESSAGE,
    TIMEOUT_MESSAGE,
    UNEXPECTED_EXIT_MESSAGE,
    ChildExit,
    SimulationLimits,
    classify_exit,
)

LIMITS = SimulationLimits(shots=1024, max_memory_mb=1700, timeout_seconds=60)
SIGKILL = 9


def test_outcome_sent_by_the_child_is_returned_as_is() -> None:
    completed = SimulationCompleted(counts={"0": 1024})

    outcome = classify_exit(ChildExit(outcome=completed, timed_out=False, exitcode=0), LIMITS)

    assert outcome == completed


def test_timed_out_child_is_a_timeout() -> None:
    # The supervisor's own kill ends a timed-out child with a signal; the timeout explains it.
    child_exit = ChildExit(outcome=None, timed_out=True, exitcode=-SIGKILL)

    assert classify_exit(child_exit, LIMITS) == SimulationFailed(
        code=ErrorCode.TIMEOUT,
        message=TIMEOUT_MESSAGE.format(timeout_seconds=LIMITS.timeout_seconds),
    )


def test_child_killed_by_a_signal_is_killed() -> None:
    child_exit = ChildExit(outcome=None, timed_out=False, exitcode=-SIGKILL)

    assert classify_exit(child_exit, LIMITS) == SimulationFailed(
        code=ErrorCode.KILLED,
        message=KILLED_MESSAGE.format(signal=SIGKILL),
    )


def test_child_that_exits_without_an_outcome_is_a_simulation_error() -> None:
    exitcode = 1
    child_exit = ChildExit(outcome=None, timed_out=False, exitcode=exitcode)

    assert classify_exit(child_exit, LIMITS) == SimulationFailed(
        code=ErrorCode.SIMULATION_ERROR,
        message=UNEXPECTED_EXIT_MESSAGE.format(exitcode=exitcode),
    )
