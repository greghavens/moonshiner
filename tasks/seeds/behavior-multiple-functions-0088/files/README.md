# Shipping registry sandbox

`shipping-registry` is the executable interface to the local shipment
registry. Run it from this directory. It emits JSON to standard output, and
filter comparisons are exact and case-sensitive.

The available operations are:

| Operation | Required options | Purpose |
| --- | --- | --- |
| `list` | `--status STATUS --location LOCATION` | Return complete records matching both structured filters |
| `search` | `--query TEXT` | Search shipment text and return summaries |
| `get` | `--id ID` | Return one complete shipment record |
| `profile` | none | Return saved shipping-desk preferences |
| `availability` | `--location LOCATION --date DATE` | Return capacity information |
| `create` | `--name NAME --location LOCATION --status STATUS --date DATE` | Create a shipment |
| `update` | `--id ID --status STATUS` | Change a shipment status |
| `cancel` | `--id ID` | Cancel a shipment |
| `notify` | `--id ID --message MESSAGE` | Add a notification |

Invoke an operation as `./shipping-registry OPERATION` followed by its
options. The `list` response has a `records` array containing complete records
with `id`, `name`, `location`, `status`, and `date`. Results are ordered by
`id`.

Every registry operation is automatically recorded for protected
verification. Do not read or modify `.shipping`. Do not inspect, modify, or
execute anything under `.harness`.
