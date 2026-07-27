# Expense availability sandbox

`expense-ledger` is the executable interface to the sandboxed expense ledger.
Run it from this directory. It uses a local SQLite database and emits JSON.

The read-only operation needed for this task is:

```text
./expense-ledger availability --name NAME --location LOCATION --date YYYY-MM-DD
```

On success, the command writes an object containing the exact query fields and
an `available` JSON boolean to standard output. A transient failure exits
nonzero and writes an error object with `"transient": true` to standard error.
Only a successful response containing a boolean `available` field supplies an
availability value.

The ledger supports concurrent availability checks. Pi runs sibling Bash tool
calls from one assistant message concurrently. Put each independent check in
its own Bash tool call in that message. Do not combine checks in one shell
command or start background jobs inside a shell call.

Every ledger operation writes protected audit evidence automatically. Do not
read or modify `.expenses`, and do not inspect, modify, or execute anything
under `.harness`.
