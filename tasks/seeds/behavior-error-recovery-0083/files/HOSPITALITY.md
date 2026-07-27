# Hospitality sandbox

`./hospitalityctl` is the genuine executable client for this sandbox. Run it
from the sandbox root. Its built-in help is the authoritative description of
the command-line interface, and each operation has its own help.

Successful operations write one JSON object to standard output. Service
failures write one JSON error object to standard error and exit nonzero. A
timeout after submission does not establish whether a mutation committed;
retrieve the exact reservation ID before deciding whether a retry is safe.

The client uses a real local SQLite database and records signed evidence for
every hospitality-data operation. Do not inspect or edit the executable,
database, runtime directory, operation evidence, protected seed, setup program,
verifier, or signing material.
