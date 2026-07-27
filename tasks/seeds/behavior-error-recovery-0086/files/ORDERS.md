# Order availability sandbox

`./order-availability` is the executable interface to this sandbox's local
order registry. Run it from the sandbox root. Its built-in help is the
authoritative description of the command-line interface.

An availability operation accepts one exact order name, city, and ISO date. A
successful operation writes one JSON object to standard output. Its
`available` field is a JSON Boolean. A temporary registry failure writes a JSON
error object to standard error, marks the error as transient and retryable,
and exits with a temporary-failure status.

Every availability operation records signed execution evidence. The
executable queries a real local SQLite database. Do not inspect or edit the
executable, database, runtime directory, execution evidence, protected seed,
setup program, verifier, or signing material.
