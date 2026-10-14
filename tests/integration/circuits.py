"""OpenQASM circuits submitted by the integration tests."""

BELL_CIRCUIT = """
OPENQASM 3.0;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cx q[0], q[1];
c = measure q;
"""

# Takes a few seconds to simulate, long enough to kill the worker running it. The mid-circuit
# measurement makes Aer simulate each shot separately; 15 qubits keeps it well inside the time limit.
SLOW_CIRCUIT = (
    'OPENQASM 3.0; include "stdgates.inc"; qubit[15] q; bit[15] c; '
    "h q; t q; c[0] = measure q[0]; h q; t q; cx q[0], q[1]; c = measure q;"
)
