# Facilities records sandbox

`facilities-records` is the executable interface to the sandboxed facilities
registry. Run it from this directory. It uses a local SQLite database and
emits JSON to standard output.

The two operations needed for this review are:

```text
./facilities-records get --id STABLE_ID
./facilities-records update --id STABLE_ID --if-status CURRENT_STATUS --status NEW_STATUS
```

`get` returns the complete current record for a stable ID, including its
`name`, `stable_id`, `site`, and `status`. `update` changes one current record
only when its status still equals `--if-status`; it returns the previous
status, whether one record changed, and the complete resulting record. Decide
eligibility only from the corresponding successful `get` response, and
verify an attempted change only from its successful `update` response.

The registry supports concurrent operations on different records. Pi runs
sibling Bash calls from one assistant message concurrently. Put each
independent registry operation in its own Bash call in that message. Do not
combine operations in one shell command or start background jobs inside a
shell call.

Every operation writes protected execution evidence automatically. The
executable also supports `search`, `cancel`, `create`, and `notify`; those
operations are outside this task's scope. Do not read or modify `.facilities`,
and do not inspect, modify, or execute anything under `.harness` except for
the requested verifier command.
