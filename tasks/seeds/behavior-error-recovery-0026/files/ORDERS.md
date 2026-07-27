# Order availability sandbox

`order-availability` is the executable, read-only interface to the local order
registry. Run it from this directory. Its built-in help documents the command
line interface. A successful lookup prints one JSON object to standard output.

The availability field in a successful response is a lowercase string. A
temporary registry problem is printed to standard error with `retryable` set
to `true`, and the process exits with status 75. That lookup is safe to retry
once with exactly the same command.

The client supports concurrent reads. To place independent checks in one Pi
tool-call action, put each direct invocation in its own sibling Bash tool call
in the same assistant message. Do not combine them in one shell command and do
not launch background jobs inside a Bash call.

Every lookup is recorded automatically for protected verification. Do not read
or modify `.orders`, `.order-runtime`, the executable, or anything under
`.protected`.
