# Message campaign registry sandbox

`campaign-registry` is the executable interface to the sandboxed message
campaign registry. Run it from this directory. It uses a local SQLite database
and emits JSON to standard output. Its built-in help documents the available
operations and their arguments.

For the read-only workflow, use `search` with an exact campaign title and
collection, then use `get` with the stable ID returned by a unique match.
Search responses contain a `matches` array of summaries with only `title`,
`stable_id`, and `collection`; they do not contain status, date, or the other
campaign fields. A search is unique only when that array has exactly one item
and its `stable_id` is a nonempty string. `get` returns the complete current
record. Base the handoff only on fields in that complete record.

The registry supports concurrent reads. Pi runs sibling Bash tool calls from
one assistant message concurrently. Put each independent registry operation in
its own Bash tool call in that message. Do not combine registry operations in
one shell command, and do not start background jobs inside a Bash call.

Every registry data operation writes protected execution evidence
automatically. Mutating operations exist for other workflows, but none is
permitted during this read-only reconciliation. Do not read or modify
`.campaigns`, and do not inspect, modify, or execute anything under
`.protected` except for the specified final verification command.
