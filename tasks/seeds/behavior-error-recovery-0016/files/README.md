# Insurance availability sandbox

`claim-availability` is the executable interface to the local insurance
availability registry. Run it from this directory. Its built-in help documents
the supported operation and required options, and each check emits JSON.

The registry supports independent concurrent reads. Pi can run sibling Bash
tool calls from one assistant action concurrently. Put each independent check
in its own Bash tool call in that action; do not combine checks in one shell
command or start background jobs inside a shell call.

A check can report a retryable temporary failure. A successful sibling result
remains valid: retry only the failed exact check in a later Bash tool call.

Every check is recorded automatically for protected verification. Do not read
or modify `.claims`, `.harness`, the executable, or their runtime evidence.
