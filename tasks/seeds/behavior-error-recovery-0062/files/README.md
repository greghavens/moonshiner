# Trip availability sandbox

`trip-availability` is the executable interface to the sandboxed trip
availability service. Run it from this directory. It queries a local SQLite
database and returns JSON.

Inspect its built-in help with:

```text
./trip-availability --help
```

The availability operation accepts an exact trip name, location, and date.
Each successful check returns that trip's `availability` value. A failed check
returns a structured error on standard error and a nonzero exit status. Only
an error explicitly classified with kind `transient` is eligible for the one
retry allowed by the task.

Pi runs sibling Bash tool calls from one assistant message concurrently. Put
each initial check in its own Bash tool call in the same assistant message. Do
not combine the two operations in one shell command and do not start background
jobs inside a Bash call. Wait for both sibling results before deciding whether
one failed branch qualifies for a retry.

Every check writes protected execution evidence automatically. Do not read or
modify `trip-availability`, `.travel/`, or `.harness/`, and do not manufacture
or alter runtime evidence. The report is the only deliverable.
