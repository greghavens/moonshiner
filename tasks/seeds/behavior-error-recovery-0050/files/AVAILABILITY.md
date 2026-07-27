# Availability sandbox

`availability-check` is the executable, read-only interface to the local
availability registry. Run it from this directory. Its built-in help documents
the command-line interface. A successful check prints one JSON object to
standard output.

The `availability` field in a successful response is a lowercase string. A
temporary registry problem is printed to standard error with `retryable` set to
`true`, and the process exits with status 75. That exact check is safe to retry
once.

The client supports concurrent reads. To place independent checks in one Pi
assistant action, put each direct invocation in its own sibling Bash tool call
in the same assistant message. Do not combine them in one shell command and do
not launch background jobs inside a Bash call.

Every check is recorded automatically for protected verification. Do not read
or modify `.availability`, `.availability-runtime`, the executable, or anything
under `.protected`.
