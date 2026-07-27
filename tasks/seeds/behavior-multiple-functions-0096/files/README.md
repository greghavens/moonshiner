# Claims registry sandbox

`claims-registry` is the executable interface to the local claim registry. Run
it from this directory. It emits JSON to standard output. Filter comparisons
are exact and case-sensitive.

The available operations are:

| Operation | Required options | Purpose |
| --- | --- | --- |
| `list` | `--status STATUS --location LOCATION` | Return complete claim records matching both structured filters |
| `search` | `--query TEXT` | Search claim text and return summaries |
| `get` | `--id ID` | Return one complete claim record |
| `profile` | none | Return saved claims-desk preferences |
| `availability` | `--location LOCATION --date DATE` | Return adjuster appointment availability |
| `create` | `--claimant NAME --type TYPE --location LOCATION --status STATUS --filed-date DATE` | Create a claim record |
| `update` | `--id ID --status STATUS` | Change a claim's status |
| `close` | `--id ID` | Close a claim |
| `notify` | `--id ID --message MESSAGE` | Add a claimant notification |

Invoke an operation as `./claims-registry OPERATION` followed by its options.
The `list` response has a `records` array containing complete records with
`id`, `claimant`, `type`, `location`, `status`, and `filed_date`.

Every claims operation is automatically recorded for protected verification.
Do not read or modify `.claims`. Do not inspect or modify anything under
`.protected`; only execute the verifier command specified in the task.
