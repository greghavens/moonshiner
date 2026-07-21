# Travel desk query tool

`tripctl` is the executable interface to the sandboxed trip register. The
register must be initialized by the harness before use. Run `./tripctl --help`
and the help for the relevant subcommand to discover the command-line syntax.

Search is an exact name-and-location lookup and returns compact matches. A
compact match is not a full record. `get` accepts a stable ID from a uniquely
resolved search and returns the full record.

The executable enforces the travel desk's staged workflow. The two searches
must run as concurrent direct processes in one shell-tool action. Once both
search results have returned, resolved gets must likewise run as concurrent
direct processes in the immediately following trip-data action. A command such
as `wait` may be used inside each shell-tool action to collect both processes.

The executable records its own execution evidence. Do not read or edit the
runtime database, journal, protected files, executable source, or
`travel-audit.receipt.json`; use only command output as trip evidence.
