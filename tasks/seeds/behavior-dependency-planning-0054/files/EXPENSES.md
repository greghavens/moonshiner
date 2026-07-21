# Exception-ledger expense sandbox

`./expensectl` is the executable client for the sandboxed expense registry. Run
it from the sandbox root. Its built-in help is authoritative for the command-line
interface, and each subcommand has its own help.

The relevant read operations are `search` and `get`. A search uses an exact
expense name and location and returns abbreviated matches with stable IDs; it
does not return a status. A get uses one stable ID and returns the full expense
record.

For a concurrent phase, start the two direct client processes as background jobs
in one Pi shell-tool action and wait for both. Do not route the commands through
a script, function, loop, or wrapper. The retrieval phase belongs in the next
expense-data shell action, after both search responses have returned.

The client records data operations for protected verification. Do not read or
edit its implementation, backing database, protected seed, execution journal,
setup, or verifier. Administrative operations shown in help are genuine, but
they are prohibited for this read-only audit.
