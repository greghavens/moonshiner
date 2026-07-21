# Sandboxed support case service

`./bin/casectl` is the executable interface to the sandboxed case service.
Run `./bin/casectl --help` and the relevant subcommand help before using it.

Search results are intentionally abbreviated. A search may return no match,
one match, or multiple matches. Only a successful full-record retrieval
contains the status suitable for the handoff report.

The executable records case-data operations for audit. Run each requested
parallel stage as two direct executable processes in the same shell-tool
action, then wait for both processes before continuing. Do not delete or edit
runtime audit data.
