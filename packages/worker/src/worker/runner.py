"""Simulation runner: simulates a circuit in a supervised child process and returns its counts
or a classified failure. It has no database or queue dependency.

The child process is what makes every circuit-caused failure observable. A simulation stuck
in Aer's C++ code can't be interrupted from Python, and an out-of-memory kill can't be caught,
but the supervising parent can kill the child on timeout and see how it ended.
"""

import os
import signal
import traceback
from collections.abc import Callable

import billiard
from billiard.connection import Connection
from pydantic import BaseModel

from common.tasks import ErrorCode
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome
from worker.simulation import simulate

STOP_GRACE_SECONDS = 5


class SimulationLimits(BaseModel, frozen=True):
    shots: int
    max_memory_mb: int
    timeout_seconds: float


class ChildExit(BaseModel):
    outcome: SimulationOutcome | None
    timed_out: bool
    exitcode: int | None


def _send_outcome(
    sender: Connection, target: Callable[..., SimulationOutcome], args: tuple[object, ...]
) -> None:
    sender.send(target(*args))


def _stop(child: billiard.Process) -> None:
    child.terminate()
    child.join(STOP_GRACE_SECONDS)
    # SIGTERM can't be ignored by Aer, but a child stuck in uninterruptible I/O survives it.
    if child.is_alive() and hasattr(signal, "SIGKILL"):
        os.kill(child.pid, signal.SIGKILL)
        child.join()


def run_in_child(
    target: Callable[..., SimulationOutcome], args: tuple[object, ...], timeout_seconds: float
) -> ChildExit:
    """Call `target(*args)` in a child process, killing it after `timeout_seconds`.

    `billiard` rather than `multiprocessing`: Celery's pool processes are daemonic, and
    `multiprocessing` forbids daemonic processes from starting children.
    """
    receiver, sender = billiard.Pipe(duplex=False)
    child = billiard.Process(target=_send_outcome, args=(sender, target, args), daemon=True)
    child.start()
    sender.close()
    try:
        # Reading before joining: a child blocked on sending a large outcome would never exit.
        # A child that dies without sending closes the pipe, which also ends the wait.
        if not receiver.poll(timeout_seconds):
            _stop(child)
            return ChildExit(outcome=None, timed_out=True, exitcode=child.exitcode)
        try:
            outcome = receiver.recv()
        except EOFError:
            outcome = None
        # The outcome is in; a child that then hangs while exiting must not hold the task.
        child.join(STOP_GRACE_SECONDS)
        if child.is_alive():
            _stop(child)
        return ChildExit(outcome=outcome, timed_out=False, exitcode=child.exitcode)
    finally:
        receiver.close()


def classify_exit(child_exit: ChildExit, limits: SimulationLimits) -> SimulationOutcome:
    if child_exit.timed_out:
        return SimulationFailed(
            code=ErrorCode.TIMEOUT,
            message=f"Simulation exceeded the time limit of {limits.timeout_seconds:g} seconds.",
        )
    if child_exit.outcome is not None:
        return child_exit.outcome
    if child_exit.exitcode is not None and child_exit.exitcode < 0:
        return SimulationFailed(
            code=ErrorCode.OUT_OF_MEMORY,
            message=(
                f"Simulation was killed by the operating system (signal {-child_exit.exitcode}), "
                "most likely for exceeding the worker's memory limit."
            ),
        )
    return SimulationFailed(
        code=ErrorCode.SIMULATION_ERROR,
        message=f"Simulation process exited unexpectedly with code {child_exit.exitcode}.",
    )


def run(circuit: str, limits: SimulationLimits) -> SimulationOutcome:
    try:
        child_exit = run_in_child(
            simulate, (circuit, limits.shots, limits.max_memory_mb), limits.timeout_seconds
        )
    except Exception as exc:
        # The supervision itself failed (the child couldn't start, or its outcome couldn't be
        # read). Recording a failure beats leaving the task pending forever.
        return SimulationFailed(
            code=ErrorCode.SIMULATION_ERROR,
            message=f"Simulation could not be run: {type(exc).__name__}.",
            traceback=traceback.format_exc(),
        )
    return classify_exit(child_exit, limits)
