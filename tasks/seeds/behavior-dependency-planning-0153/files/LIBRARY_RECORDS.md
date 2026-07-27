# Library record registry

`library-records` is the executable interface to the sandboxed circulation
registry. Run it from the repository root. It emits one JSON object to standard
output for every successful operation.

Start with its built-in help:

```text
./library-records --help
```

The task uses full-record retrieval and conditional update operations. A
retrieval returns the complete current record. A conditional update applies its
new status only when the record's current status equals the supplied comparison
status, and reports whether the change was applied. The executable's per-command
help documents the required flags.

The registry is safe for concurrent access. To satisfy a Pi parallel stage,
place each independent direct executable command in its own sibling Bash tool
call in the same assistant message. Do not combine commands or start shell
background jobs.

Every record operation emits protected audit evidence automatically. Do not read
or modify `.library/` or `.harness/`; use only this executable for record access.
The executable also offers search, create, cancel, and notify operations, which
are outside this task's allowed scope.
