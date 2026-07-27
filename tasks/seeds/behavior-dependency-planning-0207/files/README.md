# Inventory audit sandbox

`inventoryctl` is the executable interface to this workspace's local inventory
ledger. Run it from the workspace root. It uses a real local SQLite database
and emits JSON to standard output.

Consult the executable's built-in help for its command-line interface. Searches
are exact on both item name and warehouse among current records. Search results
are summaries: they establish scope and a stable ID but omit status, date, and
all other complete-record fields. A result is safe to retrieve only when its
`matches` array contains exactly one item with a nonempty `id`. Retrieval by
that returned ID produces the complete current record.

The ledger supports concurrent reads. Pi starts sibling Bash tool calls from
one assistant action concurrently. Put each independent inventory operation in
its own Bash tool call in that action. Do not combine operations into one shell
command or start background jobs inside a shell call.

Every inventory operation emits protected execution evidence. Mutating
operations exist, but this task does not authorize one. Do not read or modify
`.inventory`, and do not inspect, modify, or execute anything under
`.protected` except the verifier command named in the task.
