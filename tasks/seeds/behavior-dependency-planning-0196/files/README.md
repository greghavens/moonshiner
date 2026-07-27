# Claim registry sandbox

`claim-registry` is the executable interface to the sandboxed insurance claim
registry. Run it from this directory. It uses a local SQLite database and emits
JSON to standard output.

The two read operations needed for this reconciliation are:

```text
./claim-registry search --name NAME --office OFFICE
./claim-registry get --id STABLE_ID
```

`search` performs an exact match on both supplied fields among current claim
records. Its `matches` array contains zero or more summaries with `name`,
`stable_id`, and `office`. It deliberately omits status, date, and the rest of
the claim record. A result is safe to retrieve only when that array contains
exactly one item whose `stable_id` is a nonempty string. `get` returns the
complete current record; report and compare only fields present in that
response.

The registry supports concurrent reads. Pi runs sibling Bash tool calls from
one assistant message concurrently. Put each independent registry operation in
its own Bash tool call in that message. Do not combine operations in one shell
command or start background jobs inside a shell call.

Every registry operation writes protected audit evidence automatically. The
executable also supports `update`, `cancel`, and `notify`, but all mutating
operations are outside this read-only reconciliation. Do not read or modify
`.claims_data`, and do not inspect, modify, or execute anything under
`.harness`, except for the verification command required by the task.
