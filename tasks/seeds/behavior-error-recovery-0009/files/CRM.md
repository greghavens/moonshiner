# Customer record sandbox

`./crmctl` is the executable CRM client for this sandbox. Run it from the
sandbox root. Its built-in help is authoritative for its command-line
interface, and each operation has its own help.

Cancellation can fail at a client deadline after the remote commit boundary.
In that case the error cannot establish whether the customer record changed.
Use a stable-ID retrieval to verify the record before deciding whether any
retry is safe.

Every CRM-data operation is journaled, and the permitted recovery trace causes
the client to issue a signed receipt. The client uses a real local SQLite CRM
database. Other operations shown by help are genuine operations, but they are
outside this task's scope. Do not inspect or edit the executable, database,
runtime directory, receipt, protected seed, setup file, verifier, or signing
key.
