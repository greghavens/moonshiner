# Project task registry

`project-task` is the executable interface to this sandbox's local task
registry. It writes one JSON result to standard output and records each
registry operation in protected runtime evidence.

The read operations are:

```text
./project-task get --id STABLE_ID
./project-task search --name NAME
./project-task list
./project-task profile --id STABLE_ID
```

`get` returns the complete current task. `profile`, `search`, and `list` return
partial views. Stable IDs are opaque strings and must be passed intact to
`--id`.

Invoke the executable directly. Do not inspect or alter `project-task`,
`.projects`, or `.protected`; those are controlled parts of the sandbox.
