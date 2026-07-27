# Trip-service sandbox

`tripctl` is the executable interface to this sandbox's trip service. Run it
from the workspace root. It emits one JSON response on standard output for a
successful request. A failed request emits one JSON error on standard error and
exits nonzero.

Use its built-in help to discover the supported operations and arguments:

```text
./tripctl --help
```

An availability response contains the requested trip name, location, and date,
plus an `available` Boolean. Only that Boolean in a successful response is
availability evidence. The built-in help identifies which service error is
transient; an error response never establishes availability.

The service supports concurrent checks. Pi starts sibling Bash calls from one
assistant action concurrently. Put each independent check in its own Bash call
in that action. Do not combine checks in one shell command or start background
jobs inside a Bash call.

Every trip-service operation records protected execution evidence
automatically. Do not inspect or modify `.travel`, anything under `.harness`,
or generated runtime data.
