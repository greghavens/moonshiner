# Item register

`inventoryctl` is the executable client for the sandboxed item register. Run it
from this directory. It creates a local SQLite runtime on first data operation
and appends sealed execution evidence for every register operation. The
protected seed, runtime database, and audit material are implementation
details, not user-facing item sources.

Use `./inventoryctl --help` or a subcommand's `--help` for authoritative
options.

## Exact search

Search accepts a name and a location:

```text
./inventoryctl search --name NAME --location LOCATION
```

Both filters are required. Matching is literal and case-sensitive. The JSON
response contains a match count and an array of summary records. A summary
includes stable identity, name, location, and status, but is not a full item
record.

## Full-record retrieval

Retrieve one complete item record by a stable ID obtained from search:

```text
./inventoryctl get --id STABLE_ID
```

The JSON response contains the full record, including its stable ID, name,
location, status, date, category, quantity, unit, bin, and notes.

## Administrative operations

The executable also provides broad listing, availability checking, status
updates, and notifications for other inventory workflows. They are visible in
built-in help. Do not use them unless the task explicitly requires them.

Every data operation records sealed evidence automatically. Do not inspect,
edit, replay, move, or remove that evidence.
