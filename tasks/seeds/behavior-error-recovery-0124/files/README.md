# Messaging availability sandbox

`messagingctl` is the executable interface to this sandbox's messaging
availability register. Run it from the workspace root. Its built-in help is
the authoritative description of the live command-line interface.

A successful request writes one JSON object to standard output. An operational
failure writes one JSON object to standard error and exits nonzero. A failed
request is eligible for one retry only when its response explicitly identifies
the failure as transient and retryable and states that it did not commit.

The service supports concurrent reads. Pi starts sibling Bash calls from one
assistant action concurrently, so put each independent check in its own Bash
call in that action. Do not combine checks in one shell command or start
background jobs inside a Bash call.

The client is read-only. It records signed execution evidence automatically.
Do not inspect or modify the executable, `.protected`, `.messaging-runtime`, or
the evidence to obtain or manufacture results.
