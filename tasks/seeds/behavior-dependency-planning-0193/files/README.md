# Library catalog sandbox

`library-catalog` is the executable interface to the sandboxed library
catalog. Run it from this directory. It uses a local SQLite database and emits
JSON to standard output.

The two read operations needed for this check are:

```text
./library-catalog search --title TITLE --branch BRANCH
./library-catalog get --id STABLE_ID
```

`search` performs an exact match on both supplied fields among current catalog
records. Its `matches` array contains zero or more summaries with `title`,
`stable_id`, and `branch`. It deliberately omits status, date, and the rest of
the title record. A result is safe to retrieve only when that array contains
exactly one item whose `stable_id` is a nonempty string. `get` returns the
complete current record; report and compare only fields present in that
response.

The catalog supports concurrent reads. Pi runs sibling Bash tool calls from one
assistant message concurrently. Put each independent catalog operation in its
own Bash tool call in that message. Do not combine operations in one shell
command or start background jobs inside a shell call.

Every catalog operation writes protected audit evidence automatically. The
executable also supports `create`, `update`, `cancel`, and `notify`, but all
mutating operations are outside this read-only check. Do not read or modify
`.library`, and do not inspect, modify, or execute anything under `.harness`.
