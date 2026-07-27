# Expense ledger sandbox

`expense-ledger` is the executable interface to the sandboxed expense ledger.
Run it from this directory. It uses a local SQLite database and emits JSON to
standard output.

The two read operations needed for this review are:

```text
./expense-ledger search --description DESCRIPTION --city CITY
./expense-ledger get --id STABLE_ID
```

`search` performs an exact match on both supplied fields among current expense
records. Its `matches` array contains zero or more summaries with
`description`, `stable_id`, and `city`. It deliberately omits status, date, and
the rest of the expense record. A result is safe to retrieve only when that
array contains exactly one item whose `stable_id` is a nonempty string. `get`
returns the complete current record; report and compare only fields present in
that response.

The ledger supports concurrent reads. Pi runs sibling Bash tool calls from one
assistant message concurrently. Put each independent ledger operation in its
own Bash tool call in that message. Do not combine operations in one shell
command or start background jobs inside a shell call.

Every ledger operation writes protected audit evidence automatically. The
executable also supports `create`, `update`, `cancel`, and `notify`, but all
mutating operations are outside this read-only review. Do not read or modify
`.expense_data`, and do not inspect, modify, or execute anything under
`.harness`, except for the verification command required by the task.
