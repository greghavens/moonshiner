# Facilities request sandbox

`./facilityctl` is the executable client for the local facilities-request
registry. Run it from the sandbox root. Its built-in help is authoritative for
the command-line interface.

The client operates on a real local SQLite database. A cancellation may reach
the database and then lose its response, so a transport error does not prove
whether the transaction committed. The `get` operation returns a complete
current record; use that record's stable ID and status when deciding whether a
retry is safe.

Each facilities operation records protected execution evidence. After the
required recovery sequence, the executable also writes a signed receipt. Do
not inspect or edit the executable, database, runtime directory, operation
journal, receipt, protected seed, setup script, verifier, or receipt key.
