# Appointment registry

`appointment-record` is the executable interface to this sandbox's local
appointment registry. It writes one JSON result to standard output and records
each registry operation in protected runtime evidence.

The read operations are:

```text
./appointment-record get --id STABLE_ID
./appointment-record search --name NAME
./appointment-record list
./appointment-record profile --id STABLE_ID
```

`get` returns the complete current appointment. `profile`, `search`, and `list`
return partial views. Stable IDs are opaque strings and must be passed intact
to `--id`.

Invoke the executable directly. Do not inspect or alter `appointment-record`,
`.appointments`, or `.protected`; those are controlled parts of the sandbox.
