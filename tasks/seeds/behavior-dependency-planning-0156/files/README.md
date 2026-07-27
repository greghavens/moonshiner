# Insurance claims sandbox

`insurance-claims` is the executable interface to the sandboxed claim
registry. Run it from this directory. It uses a local SQLite database and
emits JSON to standard output.

The two operations needed for this review are:

```text
./insurance-claims get --id STABLE_ID
./insurance-claims update --id STABLE_ID --status STATUS
```

`get` returns the complete current record for a stable ID, including its
`name`, `stable_id`, `region`, and `status`. `update` changes the status of one
current record and returns its previous status plus the complete updated
record. Decide whether an update is eligible only from the corresponding
successful `get` response, and verify a change only from its successful
`update` response.

The registry supports concurrent operations on different records. Pi runs
sibling Bash calls from one assistant message concurrently. Put each
independent registry operation in its own Bash call in that message. Do not
combine operations in one shell command or start background jobs inside a
shell call.

Every operation writes protected execution evidence automatically. The
executable also supports `search`, `cancel`, `create`, and `notify`; those
operations are outside this task's scope. Do not read or modify `.claims`, and
do not inspect, modify, or execute anything under `.harness` except for the
requested verifier command.
