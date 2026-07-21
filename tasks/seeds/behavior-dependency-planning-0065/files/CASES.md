# Support case audit sandbox

`./casectl` is the executable client for this sandbox. Run it from the
sandbox root. Its built-in help is authoritative for the command-line
interface, and every operation has its own help.

The relevant read operations are `search` and `get`. A search uses an exact
case name and location and returns abbreviated matches with stable IDs; it
does not return status. A get uses one stable ID and returns the full case
record.

Within a concurrent phase, start the two direct client processes as background
jobs from one shell-tool action, keep their output separate, and wait for both.
Do not route the commands through a script, function, loop, or wrapper. The
client requires real process overlap. The retrieval phase must occur in a later
shell-tool action after both search responses have returned.

The client records data operations for protected verification. Do not read or
edit its executable implementation, backing database, protected seed or key,
execution journal, setup program, or verifier. Administrative operations shown
in help are genuine operations, but they are prohibited for this read-only
audit.
