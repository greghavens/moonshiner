# Order review sandbox

`orderctl` is the executable interface to this workspace's local order ledger.
Run it from the workspace root. It uses a real local SQLite database and emits
JSON to standard output.

Consult the executable's built-in help for its command-line interface. Search
matches are exact on both record name and city among active records. Search
results are summaries: they establish scope and a stable ID but omit status,
date, and all other full-record fields. A result is safe to retrieve only when
its `matches` array contains exactly one item with a nonempty `id`. Retrieval
by that returned ID produces the complete current record. A field that is not
present in the retrieved JSON is unknown.

The ledger supports concurrent reads. Pi starts sibling Bash tool calls from
one assistant action concurrently. Put each independent order operation in its
own Bash tool call in that action. Do not combine operations into one shell
command or start background jobs inside a shell call.

Every order operation emits protected execution evidence. Mutating operations
exist, but none is authorized for this review. Do not read or modify `.orders`,
and do not inspect, modify, or execute anything under `.protected` except the
verifier command named in the task.
