# Program task registry sandbox

`task-registry` is the executable interface to the local program task
registry. Run it from this directory. It reads the registry and emits JSON to
standard output.

The two operations needed for this audit are:

```text
./task-registry search --name NAME --location LOCATION
./task-registry get --id STABLE_ID
```

`search` performs an exact match on both supplied fields and excludes records
that the registry marks stale. Its `matches` array contains zero or more
current summaries with `name`, `stable_id`, and `location`. A branch is safe to
retrieve only when that array has exactly one item and its `stable_id` is
nonempty. `get` returns the complete stored record. Status is a complete-record
field and is not included in search summaries.

The registry is safe for concurrent reads. Pi runs sibling tool calls from one
assistant message concurrently. Issue each independent registry operation in
its own Bash-tool call in that message. Do not combine two operations in one
shell command or start background jobs inside a shell call.

Every registry operation is recorded automatically for protected verification.
The executable also exposes `list`, `preferences`, `availability`, `create`,
`update`, `cancel`, and `notify`, but the task's read-only scope forbids them.
Do not read or modify `.tasks`. Do not inspect, modify, or execute anything
under `.harness`.
