# Library registry sandbox

`library-registry` is the executable interface to the sandboxed title
registry. Run it from this directory. It uses a local SQLite database and
emits JSON to standard output.

The two read operations needed for this check are:

```text
./library-registry search --title TITLE --branch BRANCH
./library-registry get --id STABLE_TITLE_ID
```

`search` performs an exact match on both supplied fields among current title
records. Its `matches` array contains zero or more summaries with `title`,
`title_id`, and `branch`. It deliberately omits status, date, and all other
title details. A result is eligible for retrieval only when that array
contains exactly one item whose `title_id` is a nonempty string. `get` returns
the complete current title record; report and compare only fields present in
that response.

The registry supports concurrent reads. Pi runs sibling Bash calls from one
assistant message concurrently. Put each independent registry operation in
its own Bash call in that message. Do not combine operations in one shell
command or start background jobs inside a shell call.

Every registry operation writes protected audit evidence automatically. The
executable also supports state-changing operations, but those are outside this
read-only check. Do not read or modify `.catalog`, and do not inspect, modify,
or execute anything under `.harness` except this verification command after
the deliverable is complete:

```text
python3 -B .harness/verify.py
```
