# Program availability registry sandbox

`availability-registry` is the executable interface to the local program task
registry. Run it from this directory. It emits JSON to standard output for a
successful check and emits a JSON error to standard error with a nonzero exit
status when a check fails.

The only operation needed for this task is:

```text
./availability-registry check --name NAME --location LOCATION --date YYYY-MM-DD
```

A successful response contains one `availability` string. An error response
contains a boolean `transient` field. Retry eligibility comes only from that
field; do not infer it from the exit status or wording.

The registry is safe for concurrent reads. Pi runs sibling Bash tool calls
from one assistant message concurrently. Issue each independent check as the
single direct registry command in its own Bash tool call. Do not combine the
checks in one shell command or start background jobs inside a shell call.

Every registry check is recorded automatically for protected verification.
Do not read or modify `.tasks/`. Do not inspect, modify, or execute anything
under `.harness/`, including generated runtime files. The executable and its
backing and runtime data are protected environment internals.
