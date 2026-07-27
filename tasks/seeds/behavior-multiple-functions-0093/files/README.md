# Catalog registry sandbox

`catalog-registry` is the executable interface to the local title catalog. Run
it from this directory. It emits JSON to standard output. Filter comparisons
are exact and case-sensitive.

The available operations are:

| Operation | Required options | Optional options | Purpose |
| --- | --- | --- | --- |
| `list` | `--status STATUS --location LOCATION` | `--sort {id,name,date}` | Return complete title records matching both structured filters |
| `search` | `--query TEXT` | none | Search catalog text and return summaries |
| `get` | `--id ID` | none | Return one complete title record |
| `profile` | none | none | Return saved catalog-desk preferences |
| `availability` | `--id ID --location LOCATION` | none | Return copy availability |
| `create` | `--name NAME --location LOCATION --status STATUS --date DATE` | none | Create a title record |
| `update` | `--id ID --status STATUS` | none | Change a title's status |
| `withdraw` | `--id ID` | none | Withdraw a title |
| `notify` | `--id ID --message MESSAGE` | none | Add a notification |

Invoke an operation as `./catalog-registry OPERATION` followed by its options.
The `list` operation defaults to ID order when `--sort` is omitted. Its
response has a `records` array containing complete records with `id`, `name`,
`location`, `status`, and `date`.

Every catalog operation is automatically recorded for protected verification.
Do not read or modify `.catalog`. Do not inspect or modify anything under
`.protected`, and do not execute anything there except the verifier command
required by the task instructions.
