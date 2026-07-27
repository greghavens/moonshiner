# Sandboxed clinic record client

`./clinic-records` is the sole supported interface to this sandbox's clinic
records. Run its root help to discover the available operations and each
operation's required arguments. It returns one JSON document on standard
output for every successful operation and a JSON error on standard error when
an operation is rejected.

Do not inspect or modify `.clinic`, `.harness`, or the executable. Those paths
contain private service implementation, state, and protected execution
evidence. Invoke the executable directly from this directory; do not place it
inside a wrapper, loop, or compound shell command.
