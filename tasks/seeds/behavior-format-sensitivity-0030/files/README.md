# Projectdesk sandbox

`projectdesk` is the executable client for the local project registry. Run it
from this directory:

```text
./projectdesk get --id RECORD_ID
./projectdesk search --query TEXT
./projectdesk list
./projectdesk update --id RECORD_ID --status STATUS
```

`get` returns one complete record as a compact JSON object on standard output.
The other commands search summaries, enumerate records, or mutate a status.
`./projectdesk --help` and each subcommand's `--help` are also available.

Every data operation is recorded automatically for protected verification.
Do not read or alter the executable, backing data, runtime database, operation
evidence, or protected verifier. Use the client through Pi's Bash tool.
