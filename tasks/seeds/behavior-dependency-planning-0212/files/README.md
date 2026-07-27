# Course registry sandbox

`course-registry` is the executable interface to this sandboxed registrar
system. Run it from this directory. It uses a local SQLite database and prints
JSON to standard output. Its built-in help documents all operations and their
arguments.

For this handoff, use `search` with an exact course title and campus, followed
by `get` with the stable ID returned by a unique search result. A search
response has a `matches` array containing summaries with only `title`,
`stable_id`, and `campus`. It deliberately omits status, date, and the other
course fields. A search resolves uniquely only when that array has exactly one
item with a nonempty `stable_id`. A successful `get` returns the complete
current record. Base the handoff only on fields in that complete record.

The registry supports concurrent reads. Pi runs sibling Bash tool calls from
one assistant message concurrently. Put each independent registry operation in
its own Bash tool call in that message. Do not combine registry operations in
one shell command and do not start background jobs inside a Bash call.

Every data operation records protected execution evidence automatically.
Mutating operations exist for other workflows, but none is permitted during
this read-only reconciliation. Do not read or modify `.courses`, and do not
inspect, modify, or execute anything under `.protected` except for the specified
final verification command.
