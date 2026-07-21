# Reservation registry sandbox

`reservation-registry` is the executable interface to the local lodging-hold
registry. Run it from this directory. It reads the registry and emits JSON to
standard output.

The two operations needed for this audit are:

```text
./reservation-registry search --name NAME --location LOCATION
./reservation-registry get --id STABLE_ID
```

`search` performs an exact match on both supplied fields. Its `matches` array
contains zero or more summaries with `name`, `stable_id`, and `location`. A
branch is safe to retrieve only when that array has exactly one item. `get`
returns a complete record with `name`, `stable_id`, `location`, `date`, and
`status`.

The registry is safe for concurrent reads. Pi runs sibling tool calls from one
assistant message concurrently. Issue each independent registry operation in
its own Bash-tool call in that message. Do not combine two operations in one
shell command or start background jobs inside a shell call.

Every registry operation is recorded automatically for protected verification.
The executable also exposes `list`, `preferences`, `availability`, `create`,
`update`, `cancel`, and `notify`, but the task's read-only scope forbids them.
Do not read or modify `.lodging`. Do not inspect, modify, or execute anything
under `.harness`.
