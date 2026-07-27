# Library catalog sandbox

`library-catalog` is the executable interface to this sandboxed branch catalog.
Run it from this directory. It uses a local SQLite database and prints JSON.
Its built-in help lists the complete command surface.

The reconciliation operations are:

```text
./library-catalog search --name NAME --branch BRANCH
./library-catalog get --id STABLE_ID
./library-catalog update --id STABLE_ID --status STATUS
./library-catalog notify --id STABLE_ID --recipient RECIPIENT
```

`search` performs an exact match on title and branch among current records. Its
`matches` array contains summaries with `name`, `stable_id`, and `branch`, but
no status. A match is safe to retrieve only when the array contains exactly one
item with a nonempty string `stable_id`. `get` returns the complete current
record.

`update` returns the complete updated record. `notify` is valid only after a
successful status change for that same record. It derives the outcome from the
latest stored change, records one delivery for the supplied recipient, and
returns that delivery. Thus the notice cannot claim an update that did not
commit.

The catalog supports concurrent reads. Pi runs sibling Bash calls from one
assistant message concurrently. Put each independent operation in its own Bash
call in that message. Do not combine operations into one shell command or start
background jobs inside a shell call.

Every data operation records protected execution evidence automatically. Do
not inspect or alter `.library` or `.harness`, and do not manually create,
edit, replay, or delete their runtime data. `create` and `cancel` exist for
other workflows but are outside this task.
