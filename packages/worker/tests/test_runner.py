import os
import signal
import sys

from common.tasks import ErrorCode
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome
from worker.runner import (
    TIMEOUT_MESSAGE,
    SimulationLimits,
    SimulationRunner,
    classify_exit,
    run_in_child,
)

LIMITS = SimulationLimits(shots=1024, max_memory_mb=1700, timeout_seconds=60)
SHORT_TIMEOUT_SECONDS = 2

BELL_CIRCUIT = """
OPENQASM 3.0;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cx q[0], q[1];
c = measure q;
"""


def test_bell_circuit_returns_counts_over_00_and_11() -> None:
    outcome = SimulationRunner(LIMITS).run(BELL_CIRCUIT)

    assert isinstance(outcome, SimulationCompleted)
    # Subset, not equality: sampling can miss one of the two outcomes.
    assert set(outcome.counts) <= {"00", "11"}
    assert sum(outcome.counts.values()) == LIMITS.shots


def _failure(circuit: str, limits: SimulationLimits = LIMITS) -> SimulationFailed:
    outcome = SimulationRunner(limits).run(circuit)
    assert isinstance(outcome, SimulationFailed), outcome
    return outcome


def test_invalid_qasm3_fails_with_the_parse_error() -> None:
    failure = _failure("OPENQASM 3.0;\nqubit[2] q;\nnot_a_gate q[0];\n")

    assert failure.code == ErrorCode.INVALID_CIRCUIT
    assert failure.message.startswith("Invalid QASM3: ")
    assert "'not_a_gate' is not defined" in failure.message


def test_circuit_without_measurements_fails_with_the_simulator_message() -> None:
    failure = _failure('OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[2] q;\nh q[0];\n')

    assert failure.code == ErrorCode.SIMULATION_ERROR
    assert failure.message.startswith("QiskitError: No counts for experiment")


def test_circuit_too_large_for_the_memory_budget_fails_with_the_simulator_message() -> None:
    # 20 qubits need 16 MB as a statevector. The `t` gates and the entangling `cx` keep Aer
    # from choosing a cheaper method (stabilizer, matrix product state).
    circuit = (
        'OPENQASM 3.0; include "stdgates.inc"; qubit[20] q; bit[20] c; '
        "h q; t q; cx q[0], q[1]; c = measure q;"
    )

    failure = _failure(circuit, LIMITS.model_copy(update={"max_memory_mb": 8}))

    assert failure.code == ErrorCode.CIRCUIT_TOO_LARGE
    assert failure.message.startswith("Insufficient memory")
    assert "Required memory: 16M, max memory: 8M" in failure.message


def test_non_terminating_circuit_times_out() -> None:
    # The qubit is flipped to 1 and never changes, so the loop never ends.
    circuit = (
        'OPENQASM 3.0; include "stdgates.inc"; qubit[1] q; bit[1] c; '
        "x q[0]; c[0] = measure q[0]; while (c[0]) { c[0] = measure q[0]; }"
    )

    failure = _failure(circuit, LIMITS.model_copy(update={"timeout_seconds": SHORT_TIMEOUT_SECONDS}))

    assert failure == SimulationFailed(
        code=ErrorCode.TIMEOUT,
        message=TIMEOUT_MESSAGE.format(timeout_seconds=SHORT_TIMEOUT_SECONDS),
    )


def test_unexpected_simulator_error_fails_with_its_type_and_message() -> None:
    circuit = 'OPENQASM 3.0; include "stdgates.inc"; qubit[200] q; bit[200] c; h q; t q; c = measure q;'

    failure = _failure(circuit)

    assert failure.code == ErrorCode.SIMULATION_ERROR
    assert failure.message.startswith("CircuitTooWideForTarget: Number of qubits (200)")
    assert failure.traceback is not None
    assert "Traceback" not in failure.message


if sys.platform != "win32":  # Windows has no SIGKILL.

    def test_child_killed_by_a_signal_is_classified_as_killed() -> None:
        child_exit = run_in_child(_kill_self, (), LIMITS.timeout_seconds)

        assert child_exit.exitcode == -signal.SIGKILL
        outcome = classify_exit(child_exit, LIMITS)
        assert isinstance(outcome, SimulationFailed)
        assert outcome.code == ErrorCode.KILLED

    def _kill_self() -> SimulationOutcome:
        # What the kernel's OOM killer does to the child.
        os.kill(os.getpid(), signal.SIGKILL)
        raise AssertionError("unreachable")
