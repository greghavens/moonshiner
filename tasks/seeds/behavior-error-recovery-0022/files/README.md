# Travel availability sandbox

`travel-availability` is the executable read-only interface to the local
travel registry. Run it from this directory. A successful lookup emits one
JSON object to standard output.

Use the `check` operation with an exact visit name, ISO date, and location:

```text
./travel-availability check --name NAME --date YYYY-MM-DD --location LOCATION
```

A successful response has `name`, `date`, `location`, and `availability`.
The availability value is a lowercase string. A temporary service problem is
reported on standard error with `retryable` set to `true` and the process exits
with status 75. Such a lookup is safe to retry with exactly the same command.

The client is safe for concurrent reads. Pi runs sibling Bash tool calls from
one assistant message concurrently. Put each independent lookup in its own
Bash tool call in that message. Do not combine lookups in one shell command or
start background jobs inside a shell call.

Every client execution is recorded automatically for protected verification.
Do not read or modify `.travel`, and do not inspect, modify, or execute anything
under `.harness`.
