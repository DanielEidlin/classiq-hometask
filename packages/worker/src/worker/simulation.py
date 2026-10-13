"""The work done inside the runner's child process: parse, check and simulate a circuit."""

import traceback

from qiskit import QuantumCircuit, qasm3, transpile
from qiskit.circuit import ControlFlowOp
from qiskit.exceptions import QiskitError
from qiskit_aer import AerSimulator

from common.tasks import ErrorCode
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome

MAX_MESSAGE_LENGTH = 1000
# Aer reports a circuit that doesn't fit `max_memory_mb` in the result's status, without raising.
_INSUFFICIENT_MEMORY = "Insufficient memory"


def _truncate(message: str) -> str:
    if len(message) <= MAX_MESSAGE_LENGTH:
        return message
    return message[: MAX_MESSAGE_LENGTH - 3] + "..."


def _describe(exc: Exception) -> str:
    # A QiskitError's str() wraps its message in quotes.
    return exc.message if isinstance(exc, QiskitError) else str(exc)


def has_measurement(circuit: QuantumCircuit) -> bool:
    """Whether the circuit measures anything, including inside control flow (`if`, `while`)."""
    for instruction in circuit.data:
        operation = instruction.operation
        if operation.name == "measure":
            return True
        if isinstance(operation, ControlFlowOp) and any(
            has_measurement(block) for block in operation.blocks
        ):
            return True
    return False


def simulate(circuit_text: str, shots: int, max_memory_mb: int) -> SimulationOutcome:
    try:
        circuit = qasm3.loads(circuit_text)
    except Exception as exc:
        return SimulationFailed(
            code=ErrorCode.INVALID_CIRCUIT, message=_truncate(f"Invalid QASM3: {_describe(exc)}")
        )
    if not has_measurement(circuit):
        return SimulationFailed(
            code=ErrorCode.INVALID_CIRCUIT,
            message="Circuit has no measurements, so it would produce no counts.",
        )

    try:
        simulator = AerSimulator(max_memory_mb=max_memory_mb)
        result = simulator.run(transpile(circuit, simulator), shots=shots).result()
    except Exception as exc:
        return SimulationFailed(
            code=ErrorCode.SIMULATION_ERROR,
            message=_truncate(f"{type(exc).__name__}: {_describe(exc)}"),
            traceback=traceback.format_exc(),
        )

    if not result.success:
        status = result.results[0].status if result.results else result.status
        reason = status.removeprefix("ERROR:").strip()
        code = (
            ErrorCode.CIRCUIT_TOO_LARGE
            if _INSUFFICIENT_MEMORY in reason
            else ErrorCode.SIMULATION_ERROR
        )
        return SimulationFailed(code=code, message=_truncate(reason))
    counts: dict[str, int] = result.get_counts()
    return SimulationCompleted(counts=counts)
