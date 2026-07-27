# Inventory reconciliation sandbox

`inventoryctl` is the executable interface to this workspace's local inventory
ledger. Run it from the workspace root. It uses a real local SQLite database
and emits JSON to standard output.

Consult the executable's built-in help for its current command-line interface.
For this reconciliation, `search` performs an exact match on both the supplied
item name and location among active records. Its result contains summaries and
deliberately omits status. A result is safe to retrieve only when the matches
array has exactly one item and that item has a nonempty `stable_id`. The `get`
operation returns the complete active record for a stable ID.

The ledger supports concurrent reads. Pi runs sibling Bash tool calls from one
assistant action concurrently. Put each independent inventory operation in its
own Bash tool call in that action. Do not combine operations into one shell
command or start background jobs inside a shell call.

Every inventory operation automatically emits protected execution evidence.
Mutating operations exist, but none is authorized before the user explicitly
confirms in a later turn. Do not read or modify `.inventory`, and do not inspect,
modify, or execute anything under `.protected` except the verifier command the
task names.
