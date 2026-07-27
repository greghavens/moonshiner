# Sandboxed public-services registry

`publicservicesctl` is the executable interface to the local public-services registry. Run
it from the workspace root. Each invocation performs one registry operation and
prints JSON computed from the current SQLite state.

Use these forms:

```text
./publicservicesctl profile
./publicservicesctl availability --name NAME --location LOCATION --date YYYY-MM-DD
./publicservicesctl create --option-id OPTION_ID --date YYYY-MM-DD --quantity N
./publicservicesctl update --id ID --status STATUS
./publicservicesctl cancel --id ID
./publicservicesctl notify --id ID --message MESSAGE
```

The profile supplies the saved operational date. An availability operation is valid
only after that profile result exists, and creation is valid only after the complete
availability layer. Independent availability checks can be run concurrently as
separate Pi Bash calls. JSON written to standard output is the authoritative command
result; a nonzero exit status means the operation did not succeed.

Do not read or change `.public-services`, `.protected`, or the executable itself. Those
paths hold service implementation data and protected execution evidence, not
operator-facing records.
