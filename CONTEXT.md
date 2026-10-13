# Quantum Circuit Execution

A service that accepts quantum circuits, simulates them asynchronously, and reports their measurement counts.

## Language

**Circuit**:
A quantum program: qubits, the gates applied to them, and measurements into classical bits. Submitted as QASM3 text.
_Avoid_: Program, QC (except as the API field name)

**Shot**:
One execution of a circuit ending in measurement, yielding one bitstring.
_Avoid_: Run, sample

**Counts**:
The tally of how many shots produced each bitstring; the result of a task.
_Avoid_: Histogram, output, distribution

**Task**:
A client's request to simulate one circuit, tracked by a task ID from submission to a final state.
_Avoid_: Job (Qiskit uses "job" for its own internal concept), request

**Accepted**:
A task is accepted once it is durably recorded; only then does the client receive its task ID.
_Avoid_: Submitted (the client submits; the system accepts)

**Pending**:
An accepted task without a final outcome yet.
_Avoid_: Queued, in progress

**Completed**:
A final state: the task's counts are available.
_Avoid_: Done, succeeded

**Failed**:
A final state: the system has given up on the task and recorded a reason. Reported to clients with the `error` status.
_Avoid_: Errored, crashed

**Attempt**:
One delivery of a task to a worker. A task normally completes in one attempt; more occur only when a worker is lost mid-task.
_Avoid_: Retry (a retry is a later attempt, not the first)
