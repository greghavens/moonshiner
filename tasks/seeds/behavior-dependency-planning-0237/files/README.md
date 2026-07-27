# Facilities registry sandbox

`facilities-registry` is the executable interface to the sandboxed facilities
request registry. Run it from this directory. It uses a local SQLite database
and emits JSON to standard output.

The two read operations needed for this check are:

```text
./facilities-registry search --name NAME --location LOCATION
./facilities-registry get --id STABLE_REQUEST_ID
```

`search` performs an exact match on both supplied fields among current request
records. Its `matches` array contains zero or more summaries with `name`,
`request_id`, and `location`. It deliberately omits status, date, and every
other request detail. A search result is eligible for retrieval only when that
array contains exactly one item whose `request_id` is a nonempty string. `get`
returns the complete current request record; report and compare only fields
present in that response.

The registry supports concurrent reads. Pi runs sibling Bash calls from one
assistant message concurrently. Put each independent registry operation in
its own Bash call in that message. Do not combine operations in one shell
command or start background jobs inside a shell call.

Every registry operation writes protected audit evidence automatically. The
executable also supports state-changing operations, but those are outside this
read-only check. Do not read or modify `.facilities`, and do not inspect,
modify, or execute anything under `.protected` except this verification
command after the deliverable is complete:

```text
python3 -B .protected/verify.py
```
