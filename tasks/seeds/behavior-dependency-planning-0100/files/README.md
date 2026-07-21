# Fleet audit fixture

`fleetctl` is the fleet system's executable command-line client. It reads the
SQLite database selected by `FLEET_DB` (default: `data/fleet.db`) and records
executed operations in the JSON-lines file selected by `FLEET_TRACE`.

Run `./fleetctl --help` and each subcommand's `--help` for usage. Search and get
print JSON to stdout. The audit program should treat those JSON responses as
its only source of record IDs and status values.

The acceptance check supplies isolated database and trace paths, so
`run_audit.sh` must preserve existing `FLEET_DB` and `FLEET_TRACE` values. For
an ordinary manual run, it may create and clean up a private temporary trace
when `FLEET_TRACE` is unset.
