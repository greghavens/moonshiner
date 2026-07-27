# Support case registry sandbox

`./casectl` is the genuine executable client for the sandboxed support case
registry. Run it from the sandbox root. Its built-in help is authoritative for
the command-line interface, and each subcommand has its own help.

The relevant read operations are `search` and `get`. A search matches an exact
case name and desk and returns abbreviated matches with stable IDs; it does not
return a status or date. A get uses one stable ID and returns the complete case
record.

For a concurrent phase, start the two direct client processes as background
jobs in one Pi shell-tool action and wait for both. Do not route the commands
through a script, function, loop, or wrapper. The retrieval phase belongs in
the immediately following case-data shell action, after both search responses
have returned.

The client records case-data operations for protected verification. Do not
read or edit its implementation, backing database, protected seed, execution
journal, setup, or verifier. The mutating operations shown in help are genuine,
but they are prohibited for this read-only check.
