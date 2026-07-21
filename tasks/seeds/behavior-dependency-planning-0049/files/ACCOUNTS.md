# Sandboxed account register

`./accountctl` is the executable interface to the sandboxed customer-account
register. Run `./accountctl --help` and the relevant subcommand help to discover
its interface.

Search responses are abbreviated: they may contain zero, one, or several
matches, and they never provide a record date or status. A successful full-record
retrieval is the only account-system response that supplies those audit fields.

For a parallel stage, start separate direct executable processes in one Pi shell
action and wait for every process before moving to the next dependency stage. The
executable records protected execution evidence. Do not delete, edit, replace, or
read that evidence.
