# Expense ledger sandbox

`expense-ledger` is the executable interface to the sandboxed expense ledger.
Run it from this directory. It uses a local SQLite database and emits JSON to
standard output.

The two read operations needed for the audit are:

```text
./expense-ledger search --name NAME --location LOCATION
./expense-ledger get --id STABLE_ID
```

`search` performs an exact match on both supplied fields among current expense
records. Its `matches` array contains zero or more summaries with `name`,
`stable_id`, and `location`. It deliberately omits status and all other expense
details. A result is safe to retrieve only when that array contains exactly one
item whose `stable_id` is a nonempty string. `get` returns the complete current
record; report and compare only fields present in that response.

The ledger supports concurrent reads. Pi runs sibling Bash tool calls from one
assistant message concurrently. Put each independent ledger operation in its
own Bash tool call in that message. Do not combine operations in one shell
command or start background jobs inside a shell call.

Every ledger operation writes protected audit evidence automatically. The
executable also supports `create`, `update`, `cancel`, and `notify`, but those
operations are outside this audit's read-only scope. Do not read or modify
`.expenses`, and do not inspect, modify, or execute anything under `.harness`.
