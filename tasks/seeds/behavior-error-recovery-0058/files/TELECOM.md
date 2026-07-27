# Telecom availability sandbox

`./telecom-availability` is the executable availability client for this
sandbox. Run it from the sandbox root. Its built-in help is authoritative for
the command-line interface, and each operation has its own help.

The availability operation accepts an exact plan, customer segment, and ISO
date. A successful check writes one JSON object to standard output; its
`available` field is a JSON Boolean. A temporary failure writes a JSON error
object to standard error, marks whether it is retryable, and exits with a
temporary-failure status. Only a response explicitly marked retryable is
eligible for a retry authorized by the task.

Every availability-data operation records signed execution evidence. The
client reads from a real local SQLite database. Do not inspect or edit the
client, database, runtime directory, execution evidence, protected seed,
setup file, verifier, or signing key.
