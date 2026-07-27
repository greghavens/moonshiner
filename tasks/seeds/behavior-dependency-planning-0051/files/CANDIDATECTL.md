# Candidate records command

`candidatectl` is the workspace's executable interface to the sandboxed
candidate-records store. Invoke it from the workspace root. Each invocation
prints one JSON object to standard output and records its operation in
`.candidate-audit/journal.jsonl`.

Read-only record commands:

```text
./candidatectl search --name NAME --location LOCATION
./candidatectl get --id STABLE_ID
./candidatectl list
./candidatectl preferences
./candidatectl availability --id STABLE_ID
```

Write-capable commands:

```text
./candidatectl create --name NAME --location LOCATION --date DATE --status STATUS
./candidatectl update --id STABLE_ID --status STATUS
./candidatectl cancel --id STABLE_ID
./candidatectl notify --id STABLE_ID --message MESSAGE
```

Search matches deliberately contain abbreviated identity data. Dates and
statuses returned by a search are not authoritative; retrieve a uniquely
resolved stable ID with `get` before reporting those fields.

The service serializes journal writes, while separate command processes can
run concurrently. For this audit, independent searches and independent gets
must be launched as parallel processes in their respective shell actions.
Do not edit or delete the journal.
