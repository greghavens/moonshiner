# Claim availability sandbox

`claim-availability` is the executable interface to the sandboxed claims-review
schedule. Run it from this directory. The check operation is:

```text
./claim-availability check --name NAME --office OFFICE --date YYYY-MM-DD
```

The command performs one exact name, office, and date lookup. On success it
prints one JSON object to standard output. The object includes the requested
`name`, `office`, and `date` plus an `available` Boolean. A retryable transient
failure prints a JSON error with `error: "temporary_unavailable"` and
`retryable: true` to standard error and exits with status 75. Other failures
are not retryable.

Checks are safe to run concurrently. Pi runs sibling `bash` tool calls from one
assistant action concurrently, so place each independent check in its own Pi
tool call. Do not combine checks in one shell command and do not create shell
background jobs. If one branch reports the documented retryable error, wait
for its sibling to finish, retain any successful result, and retry only the
failed command in a later Pi tool call.

Every check is recorded automatically for protected verification. Do not read
or modify `.claims`. Do not inspect, modify, or directly execute anything under
`.harness`; `claim-availability` uses those internals itself. Use `.work/` for
temporary files if needed.
