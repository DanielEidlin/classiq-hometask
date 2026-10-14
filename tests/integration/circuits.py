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

# The qubit is flipped to 1 and never changes, so the loop never ends: only the worker's time
# limit stops it.
NON_TERMINATING_CIRCUIT = (
    'OPENQASM 3.0; include "stdgates.inc"; qubit[1] q; bit[1] c; '
    "x q[0]; c[0] = measure q[0]; while (c[0]) { c[0] = measure q[0]; }"
)
