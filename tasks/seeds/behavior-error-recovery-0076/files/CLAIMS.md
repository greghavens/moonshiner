# Claim availability sandbox

`claim-availability` is the executable interface to the local claims system.
Run it from this directory. Its built-in help describes the current
command-line interface.

A successful availability check writes one JSON object to standard output. It
contains the claim, office, date, and an `available` Boolean. A failed check
writes one JSON error object to standard error and exits nonzero. A failure is
eligible for the task's single selective retry only when its output marks both
`transient` and `retryable` as `true`.

The system supports concurrent independent reads. Pi can issue sibling Bash
tool calls from one assistant action concurrently. Put each check in its own
direct Bash call; do not combine checks in one shell command or create
background jobs inside a Bash call.

Each check is journaled automatically for protected verification. Do not read
or change `.claims/`. Do not inspect or modify anything under `.harness/`,
including generated runtime files. Execute only the verifier command required
by the task; do not execute other `.harness/` content. The executable, backing
data, and runtime journal are protected environment internals.
