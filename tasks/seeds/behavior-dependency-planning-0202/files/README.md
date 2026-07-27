# Trip records sandbox

`trip-records` is the executable interface to the sandboxed trip registry.
Run it from this directory. It uses a local SQLite database and writes JSON to
standard output.

The read operations for a record review are:

```text
./trip-records search --name NAME --location LOCATION
./trip-records get --id STABLE_ID
```

`search` matches both supplied fields exactly among current trip records. Its
`matches` array contains summaries with only `name`, `stable_id`, and
`location`; it does not reveal a trip's status or date. A search is uniquely
resolved only when that array has exactly one item and the item's `stable_id`
is a nonempty string. `get` returns the complete current record for a stable
ID. Conclusions must use fields in that complete response.

The registry supports concurrent reads. Pi runs sibling Bash calls from one
assistant message concurrently. Put each independent registry operation in
its own Bash call in that message. Do not combine operations in one shell call
or launch background processes from the shell.

Every registry operation automatically records protected execution evidence.
The executable also implements `update`, `cancel`, and `notify`, but those
operations are outside this read-only review. Do not inspect or change
`trip-records`, `.trips`, or `.harness` directly.
