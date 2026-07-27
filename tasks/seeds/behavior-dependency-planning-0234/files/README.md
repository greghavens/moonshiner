# Expense registry sandbox

`expense-registry` is the executable interface to the sandboxed expense
registry. Run it from this directory. It uses a local SQLite database and
emits JSON to standard output.

The two read operations needed for this review are:

```text
./expense-registry search --description DESCRIPTION --city CITY
./expense-registry get --id STABLE_EXPENSE_ID
```

`search` performs an exact match on both supplied fields among current expense
records. Its `matches` array contains zero or more summaries with
`description`, `expense_id`, and `city`. It deliberately omits status, date,
and all other expense details. A result is eligible for retrieval only when
that array contains exactly one item whose `expense_id` is a nonempty string.
`get` returns the complete current expense record; report and compare only
fields present in that response.

The registry supports concurrent reads. Pi runs sibling Bash calls from one
assistant message concurrently. Put each independent registry operation in
its own Bash call in that message. Do not combine operations in one shell
command or start background jobs inside a shell call.

Every registry operation writes protected audit evidence automatically. The
executable also supports state-changing operations, but those are outside this
read-only review. Do not read or modify `.expenses`, and do not inspect,
modify, or execute anything under `.harness` except this verification command
after the deliverable is complete:

```text
python3 -B .harness/verify.py
```
