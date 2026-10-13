"""Simulation runner: parses a QASM3 circuit and simulates it with Aer."""

from qiskit import qasm3, transpile
from qiskit_aer import AerSimulator


def run(qasm: str, shots: int) -> dict[str, int]:
    """Simulate the circuit and return its counts."""
    circuit = qasm3.loads(qasm)
    simulator = AerSimulator()
    result = simulator.run(transpile(circuit, simulator), shots=shots).result()
    counts: dict[str, int] = result.get_counts()
    return counts
