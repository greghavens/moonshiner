# Account registry sandbox

`account-registry` is the executable interface to this sandboxed CRM account
registry. Run it from this directory. It uses a local SQLite database and
prints JSON to standard output. Its built-in help documents the operations and
their arguments.

For this check, use `search` with an exact account name and region, followed by
`get` with the stable ID returned by a unique search result. A search response
has a `matches` array containing summaries with only `name`, `stable_id`, and
`region`. It deliberately omits status, date, and the remaining account fields.
A search resolves uniquely only when that array has exactly one item with a
nonempty `stable_id`. A successful `get` returns the complete current record.
Base the report only on fields in that complete record.

The read-only command forms are:

```text
./account-registry search --name ACCOUNT_NAME --region REGION
./account-registry get --id STABLE_ID
```

The registry supports concurrent reads. Pi runs sibling Bash tool calls from
one assistant message concurrently. Put each independent registry operation in
its own Bash tool call in that message. Do not combine registry operations in
one shell command and do not start background jobs inside a Bash call.

Every data operation records protected execution evidence automatically.
Mutating operations exist for other workflows, but none is permitted during
this read-only check. Do not read or modify `.accounts`, and do not inspect,
modify, or execute anything under `.protected` except for the specified final
verification command.
