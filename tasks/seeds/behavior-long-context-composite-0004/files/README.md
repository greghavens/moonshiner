# Message registry

`messagectl` is the command-line client for the sandboxed message registry.
Run it from this directory. It creates a local SQLite runtime on first use and
records sealed execution evidence for every data operation. The protected seed,
runtime database, and audit material are implementation details, not
user-facing data sources.

Use `./messagectl --help` or a subcommand's `--help` for the executable's
authoritative options.

## Exact search

Search accepts both a name and a location:

```text
./messagectl search --name NAME --location LOCATION
```

Both filters are required. Matching is literal and case-sensitive. The result
is one JSON object containing `match_count` and a `matches` array of summary
records. A search summary includes stable identity, name, and location, but is
not the complete record.

## Full-record retrieval

Retrieve one complete record by a stable ID obtained from search:

```text
./messagectl get --id STABLE_ID
```

The JSON response contains a `record` object. Full records include the stable
ID, name, location, status, date, audience, channel, owner, template version,
and notes.

## Other registry operations

The client also exposes broad listing, profile, availability, creation,
updating, cancellation, and notification operations for other administrative
workflows. They are visible in built-in help. Do not use them unless the task
explicitly calls for them.

Every data operation appends sealed evidence automatically. Do not inspect,
edit, move, replay, or remove that evidence.
