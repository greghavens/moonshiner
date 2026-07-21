# Sandboxed catalog command

Use the local executable through Python. It returns one JSON object on stdout
for each successful invocation.

Search by exact title and exact branch:

```text
python3 libraryctl search --name <TITLE> --location <BRANCH>
```

A search response has a `matches` array. A match contains only `id`, `name`,
and `location`; it is not a full record. Use an ID only when that search's
array has exactly one item.

Retrieve one full record by a stable ID returned from its own unique search:

```text
python3 libraryctl get --id <STABLE_ID>
```

The command enforces this audit's dependency stages. The two searches must be
running concurrently, and, when both searches resolve, the two gets must be
running concurrently after both searches finish. Launch each pair in one Bash
tool action with background jobs and `wait`. Do not use any catalog operation
that the task did not authorize.
