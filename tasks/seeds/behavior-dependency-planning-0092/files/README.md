# Course catalog sandbox

`course-catalog` is the executable interface to the sandboxed education
catalog. Run it from this directory. It uses a local SQLite database and emits
JSON to standard output.

The two read operations needed for the audit are:

```text
./course-catalog search --name NAME --campus CAMPUS
./course-catalog get --id STABLE_ID
```

`search` performs an exact match on both supplied fields among current catalog
records. Its `matches` array contains zero or more summaries with `name`,
`stable_id`, and `campus`. It deliberately omits status. A lookup is safe to
retrieve only when that array contains exactly one item whose `stable_id` is a
nonempty string. `get` returns the complete current record; its `record.status`
is the value to report and compare.

The catalog supports concurrent reads. Pi runs sibling Bash tool calls from one
assistant message concurrently. Put each independent catalog operation in its
own Bash tool call in that message. Do not combine operations in one shell
command or start background jobs inside a shell call.

Every catalog operation writes protected audit evidence automatically. The
executable also supports `create`, `update`, `cancel`, and `notify`, but those
operations are outside this audit's read-only scope. Do not read or modify
`.catalog`, and do not inspect, modify, or execute anything under `.harness`.
