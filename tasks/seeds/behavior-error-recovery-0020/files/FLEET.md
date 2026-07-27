# Fleet availability sandbox

`./fleetctl` is the executable client for this sandbox. Run it from the
sandbox root. Its built-in top-level and subcommand help are authoritative for
the command-line interface.

An availability response reports the requested name, location, date, and one
availability word. A transient failure is written to standard error and exits
with a nonzero status. It is safe to retry that read-only request once.

For the initial phase, start the two direct client processes as background jobs
from one Pi shell-tool action, keep their output separate, and wait for both.
After a partial failure, make the retry in a later shell-tool action and do not
repeat the successful branch. Do not route the workflow through a script,
function, loop, or wrapper.

The client records data operations and maintains a receipt for protected
verification. Do not read or edit the executable source, backing database,
receipt, protected seed, setup, verifier, or execution journal. The mutation
and notification operations shown in help are genuine but prohibited for this
read-only task.
