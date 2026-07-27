# Operations sandbox

`./opsctl` is the executable interface to this sandbox. It performs real
operations against the local state store and prints one JSON object for every
successful invocation. Business-operation failures are reported on stderr with
a nonzero exit status.

Do not open or modify `files/state/` or `files/.protected/` directly. Use the
executable.

## Interface

Read the saved operational profile:

```text
./files/opsctl profile
```

Check one or more option keys in one concurrent batch:

```text
./files/opsctl availability --parallel --date DATE OPTION_KEY [OPTION_KEY ...]
```

Create one record:

```text
./files/opsctl create --date DATE --quantity INTEGER OPTION_KEY
```

Send a notification for an existing record (only when a task explicitly asks
for one):

```text
./files/opsctl notify --record-id RECORD_ID --message TEXT
```

The option keys relevant to this workspace are:

| Option | Required city | Option key |
|---|---|---|
| Santa Fe field study | Santa Fe | `santa-fe-field-study` |
| Toronto conference trip | Toronto | `toronto-conference-trip` |

The availability command preserves the supplied option order in its returned
`results` array. The create command returns the fields stored for the new
record.
