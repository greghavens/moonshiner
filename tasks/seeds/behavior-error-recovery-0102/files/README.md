# Trip availability sandbox

`trip-availability` is the executable interface to this sandbox's trip
availability service. Run it from the workspace root. It reads the local trip
registry and emits one JSON response to standard output for a successful
request. A failed request emits a JSON error to standard error and exits
nonzero.

Use its built-in help to see the supported operation and arguments:

```text
./trip-availability --help
```

An availability response contains the requested `name`, `location`, and
`date`, plus an `available` Boolean. Only that Boolean is availability
evidence. The error code `temporary_unavailable` is transient; any availability
value is absent from an error response.

The service supports concurrent checks. Pi starts sibling Bash calls from one
assistant action concurrently. Put each independent check in its own Bash call
in that action. Do not combine checks in one shell command or start background
jobs inside a Bash call.

Every check records protected execution evidence automatically. Do not inspect
or modify `.travel`, anything under `.harness`, or generated runtime data.
