# Sandboxed shipment service

`./bin/shipmentctl` is the executable interface to the sandboxed shipment
service. Run `./bin/shipmentctl --help` and the relevant subcommand help to
discover how to use it.

Search responses are abbreviated and never contain a status. Depending on the
stored data, an exact name-and-location search may return zero, one, or several
matches. Only a successful full-record retrieval supplies a status suitable
for the exception board.

For each requested parallel stage, start two direct executable processes in
one Pi shell-tool action and wait for both before continuing. The executable
keeps protected execution evidence. Do not delete, edit, or replace that
evidence.
