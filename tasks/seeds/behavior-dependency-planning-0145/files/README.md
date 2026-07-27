# Support registry sandbox

`support-registry` is the executable interface to the sandboxed support
system. Run it from this directory. It operates on a local SQLite database and
prints JSON computed from the current database to standard output.

The required operations are:

```text
./support-registry profile
./support-registry availability --name NAME --location LOCATION --date DATE
./support-registry create --name NAME --location LOCATION --date DATE --quantity INTEGER
```

`profile` returns the saved operational profile. `availability` performs one
exact name, location, and date lookup and returns an `available` Boolean for
that scope. `create` inserts one support record and returns the stored record.
Arguments containing spaces must be shell-quoted.

The registry supports concurrent availability reads. Pi executes sibling Bash
calls from one assistant message concurrently. Put each independent
availability operation in its own Bash call in that message. Do not combine
the checks in one shell call and do not launch shell background jobs.

Every registry operation records protected execution evidence automatically.
The executable also has unrelated read and mutation operations, but they are
outside this task's scope. Do not read or modify `.support`, and do not inspect,
modify, or execute anything under `.harness` except the verifier command named
in the task.
