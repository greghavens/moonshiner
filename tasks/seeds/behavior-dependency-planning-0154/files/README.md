# Expense registry sandbox

`expense-registry` is the executable interface to the sandboxed expense
record registry. Run it from this directory. It uses a local SQLite database
and emits JSON to standard output.

The two operations needed for this reconciliation are:

```text
./expense-registry get --id STABLE_ID
./expense-registry update --id STABLE_ID --status STATUS
```

`get` returns the complete current record for a stable ID, including its
`name`, `stable_id`, `location`, and `status`. `update` changes the status of
one current record and returns its previous status plus the complete updated
record. Decide whether an update is eligible only from the corresponding
successful `get` response, and verify a change only from its successful
`update` response.

The registry supports concurrent operations on different records. Pi runs
sibling Bash calls from one assistant message concurrently. Put each
independent registry operation in its own Bash call in that message. Do not
combine operations in one shell command or start background jobs inside a
shell call.

Every operation writes protected execution evidence automatically. The
executable also supports `search`, `list`, `profile`, `availability`, `create`,
`cancel`, and `notify`; they are outside this task's scope. Do not read or
modify `.expenses`, and do not inspect, modify, or execute anything under
`.harness` except for the requested verifier command.
