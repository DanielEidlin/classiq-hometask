from qiskit import qasm3

from worker.simulation import has_measurement

HEADER = 'OPENQASM 3.0; include "stdgates.inc"; qubit[1] q; bit[1] c; bit[1] d; '


def test_circuit_with_a_measurement_has_one() -> None:
    assert has_measurement(qasm3.loads(HEADER + "h q[0]; c[0] = measure q[0];"))


def test_circuit_without_measurements_has_none() -> None:
    assert not has_measurement(qasm3.loads(HEADER + "h q[0];"))


def test_measurement_inside_control_flow_counts() -> None:
    circuit = qasm3.loads(HEADER + "if (c[0]) { d[0] = measure q[0]; }")

    assert has_measurement(circuit)
