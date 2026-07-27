# Expense lookup sandbox

`expensectl` is the executable interface to this sandboxed expense ledger. Run
it from the workspace root. It uses a local SQLite ledger and emits JSON on
standard output.

For this task, the two relevant read operations are:

```text
./expensectl search --name NAME --location LOCATION
./expensectl get --id STABLE_ID
```

`search` performs an exact match on both fields among current records. Its
`matches` array contains summaries with `name`, `location`, and `stable_id`;
search deliberately omits status, date, amount, and other complete-record
fields. A search is safe to advance only when `matches` has exactly one item
whose `stable_id` is a nonempty string. `get` returns the complete current
record for one stable ID.

Search and get are dependent operations. Let the search Bash call finish,
inspect its JSON response, and then make a new Bash call for get. Do not join
them in one shell program or pipeline.

The executable maintains protected execution evidence automatically. It may
initialize a generated runtime copy of the supplied ledger; that does not
change any expense record. Do not read or modify `.expense-store`, and do not
inspect, modify, or execute anything under `.harness`. The ledger also exposes
other read and write operations, but they are outside this task's read-only
scope.
