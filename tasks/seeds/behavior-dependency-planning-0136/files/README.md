# Sandboxed claims registry

`claimsctl` is the executable interface to the local claims-intake registry. Run it
from the workspace root. Each invocation performs one registry operation and prints
JSON computed from the current SQLite state.

Use these forms:

```text
./claimsctl profile
./claimsctl availability --name NAME --location LOCATION --date YYYY-MM-DD
./claimsctl create --name NAME --location LOCATION --date YYYY-MM-DD --quantity N
./claimsctl update --id ID --status STATUS
./claimsctl cancel --id ID
./claimsctl notify --id ID --message MESSAGE
```

The profile supplies the saved operational date. An availability operation is valid
only after that profile result exists, and creation is valid only after the complete
availability layer. Independent availability checks can be run concurrently as
separate Pi Bash calls. JSON written to standard output is the authoritative command
result; a nonzero exit status means the operation did not succeed.

Do not read or change `.claims`, `.protected`, or the executable itself. Those paths
hold service implementation data and protected execution evidence, not operator-facing
records.
