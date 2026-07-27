# Expense registry sandbox

`expense-registry` is the executable interface to this sandboxed expense
registry. Run it from this directory. It uses a local SQLite database and
prints JSON to standard output. Its built-in help documents all operations and
their arguments.

For a read-only lookup, search with an exact expense name and city. A search
response contains summaries only: name, city, and stable ID. It deliberately
omits status, date, and all other expense fields. A search resolves uniquely
only when its `matches` array has exactly one item with a nonempty `stable_id`.
Retrieve a complete current record with that returned ID. A complete record
contains every value stored for that expense, but fields whose values are
missing are omitted from the JSON object.

The read-only command forms are:

```text
./expense-registry search --name EXPENSE_NAME --city CITY
./expense-registry get --id STABLE_ID
```

The registry supports concurrent reads. Pi runs sibling Bash tool calls from
one assistant message concurrently. Put each independent registry operation in
its own Bash tool call in that message. Do not combine registry operations in
one shell command and do not start background jobs inside a Bash call.

Every data operation records protected execution evidence automatically.
Mutating operations exist for other workflows, but none is permitted during
this read-only review. Do not read or modify `.expenses`, and do not inspect,
modify, or execute anything under `.protected` except for the specified final
verification command.
