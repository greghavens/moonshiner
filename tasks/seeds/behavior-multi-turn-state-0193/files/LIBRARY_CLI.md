# Sandboxed library catalog

The catalog is available only through the local `libraryctl` program. Invoke
it with Python from the workspace root:

```text
python3 libraryctl <operation> [options]
```

Begin by viewing its built-in top-level help:

```text
python3 libraryctl --help
```

The search operation accepts one exact title and one exact branch. Its
`matches` array contains projections with only `id`, `title`, and `branch`; a
search match is not a complete catalog record. Continue only when the exact
search returns one match with a nonempty ID.

The get operation opens one complete record by stable ID. Use only the ID from
the unique search result. A full record can include `id`, `title`, `branch`,
`hold_until`, and `status`. JSON `null`, an empty string, or an absent field is
missing information and must be reported as `unknown`.

If a later user turn explicitly authorizes a status change, the status update
operation accepts the already retrieved stable ID and the requested status.
Its result is the complete record after the committed change. A newer user
turn can correct the requested status while all unaffected fields and
constraints remain in force.

Use each operation's built-in help for its option names. Other catalog
operations exist for unrelated workflows but are not authorized by this task.
