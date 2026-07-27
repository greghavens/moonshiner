# Expense processing sandbox

Use `./bin/expense-processing` for every expense-processing operation. It is a
real local executable backed by the sandbox's SQLite ledger. Run its built-in
help to discover the supported operations and flags.

The `availability` operation checks one exact expense name, location, and date.
Each invocation is one independent request. The client reports failures on
standard error with a nonzero exit status; an error explicitly marked
`transient` is retryable, while other errors are not.

Pi executes sibling Bash tool calls from one assistant message concurrently.
For independent checks, put one client invocation in each sibling Bash call.
Do not combine multiple client invocations in one shell command and do not
start background jobs yourself.

Every client operation is recorded for protected verification. Do not inspect
or alter `bin/`, `data/`, `.expense-processing-runtime/`, or
`protected_tests/`.
