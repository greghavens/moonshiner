# Project registry sandbox

`project-registry` is the genuine executable interface to the local project
registry. Run it from this directory. It reads the registry and emits JSON to
standard output.

The two operations needed for this audit are:

```text
./project-registry search --name NAME --location LOCATION
./project-registry get --id STABLE_ID
```

`search` performs an exact match on both supplied fields. Its `matches` array
contains zero or more summaries with `name`, `stable_id`, and `location`. A
branch is safe to retrieve only when that array has exactly one item. `get`
returns a complete record with `name`, `stable_id`, `location`, `date`, and
`status`.

The registry is safe for concurrent reads. Pi runs sibling tool calls from one
assistant message concurrently. Issue each independent registry operation in
its own shell-tool call in that message. Do not combine two operations in one
shell command or start background jobs inside a shell call. Use `.work/` for
temporary captures if needed; it is ignored by the sandbox.

Every registry operation is recorded automatically for protected verification.
The executable also exposes `list`, `profile`, `availability`, `create`,
`update`, `cancel`, and `notify`, but the task's read-only scope explicitly
forbids them. Do not read or modify `.registry` or `.harness` directly.
