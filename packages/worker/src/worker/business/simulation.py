"""The work done inside the runner's child process: parse and simulate a circuit."""

import traceback

from qiskit import qasm3, transpile
from qiskit.exceptions import QiskitError
from qiskit.result import Result
from qiskit_aer import AerSimulator

from common.domain.tasks import ErrorCode
from worker.domain.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome

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


def _rejection(result: Result) -> SimulationFailed:
    status = result.results[0].status if result.results else result.status
    reason = status.removeprefix("ERROR:").strip()
    code = (
        ErrorCode.CIRCUIT_TOO_LARGE if _INSUFFICIENT_MEMORY in reason else ErrorCode.SIMULATION_ERROR
    )
    return SimulationFailed(code=code, message=_truncate(reason))


def simulate(circuit_text: str, shots: int, max_memory_mb: int) -> SimulationOutcome:
    try:
        circuit = qasm3.loads(circuit_text)
    except Exception as exc:
        return SimulationFailed(
            code=ErrorCode.INVALID_CIRCUIT, message=_truncate(f"Invalid QASM3: {_describe(exc)}")
        )

    try:
        simulator = AerSimulator(max_memory_mb=max_memory_mb)
        result = simulator.run(transpile(circuit, simulator), shots=shots).result()
        if not result.success:
            return _rejection(result)
        counts: dict[str, int] = result.get_counts()
    except Exception as exc:
        return SimulationFailed(
            code=ErrorCode.SIMULATION_ERROR,
            message=_truncate(f"{type(exc).__name__}: {_describe(exc)}"),
            traceback=traceback.format_exc(),
        )
    return SimulationCompleted(counts=counts)
