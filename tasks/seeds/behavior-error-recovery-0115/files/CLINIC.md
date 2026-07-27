# Clinic appointment sandbox

`appointmentctl` is the executable interface to the local appointment
registry. Run it from this directory and use its built-in help to discover
the available operations and the required deadline for state-changing
requests.

Registry operations emit JSON to standard output. Errors use standard error
and a nonzero exit status. A deadline can expire after a request reaches the
registry, so a timeout does not reveal whether a write committed. Resolve
that uncertainty by retrieving the exact stable ID before deciding whether
one retry is safe.

Every registry operation and help discovery are recorded automatically for
protected verification. Do not read or modify `.clinic`, `.protected`, the
executable implementation, backing state, runtime state, or execution
journal. Do not replace executable calls with direct file edits.
