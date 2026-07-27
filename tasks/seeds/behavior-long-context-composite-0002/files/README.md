# Tripdesk archive

`tripdesk` is the read-only command-line interface to the sandboxed travel
archive. Run it from this directory. The protected seed and audit material are
implementation details and are not user-facing data sources.

## Search

Use a jointly scoped exact search:

```text
./tripdesk search --name NAME --city CITY --status STATUS
```

All three filters are required. Matching is literal and case-sensitive. A
successful search prints one compact JSON object whose `matches` array contains
summary records. Search does not return the full record.

## Full details

After a search identifies one summary record, retrieve the complete record by
the stable ID returned in that summary:

```text
./tripdesk get --id STABLE_ID
```

A successful retrieval prints one compact JSON object with `record_type`,
stable identity, dates, location, status, coordinator, venue, focus,
participant count, and notes.

There are no create, update, archive, or delete commands. Every data operation
appends sealed execution evidence automatically. Do not inspect, edit, move, or
remove that evidence.
