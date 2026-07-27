# Sandboxed shipment register

`shipmentctl` is the executable interface to the local shipment register. Run
it directly from the repository root. It writes compact JSON to standard
output and diagnostic text to standard error.

Use an exact, fully scoped search:

```text
./shipmentctl search --name TEXT --location TEXT --status TEXT
```

All three comparisons are exact and case-sensitive. The result is an object
with a `matches` array. Each match is only a search summary containing `id`,
`name`, and `location`; it is not a full shipment record. A stable ID is
resolved only when `matches` contains exactly one object and that object's
`id` is nonempty.

Retrieve a full record using a stable ID returned by the preceding unique
search:

```text
./shipmentctl get --id STABLE_ID
```

The result is an object whose `record` value is the full shipment record, or
`null` if the ID no longer exists. The executable enforces the search-before-
get dependency and records genuine process execution for the protected
verifier.

`./shipmentctl --help` and each subcommand's `--help` are safe to inspect.
Shipment data and implementation files are not documentation. Do not read the
runtime database, execution journal, receipt, executable source, or protected
files.
