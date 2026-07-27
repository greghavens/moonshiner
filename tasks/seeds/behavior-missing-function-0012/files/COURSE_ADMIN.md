# Course administration sandbox

`bin/educationctl` is the executable client for this sandbox's local course
administration service. Run it from the sandbox root. Every service operation
is recorded in a protected execution audit, so reading or editing the backing
files is not a substitute for executing the client.

The deployment exposes operations in stages:

1. Start with `./bin/educationctl capabilities`.
2. Read that response literally. It lists the operations available at that
   point and, when a requested operation is unavailable, the one supported
   handoff operation.
3. Invoke only a currently exposed operation. A successful handoff response
   lists the newly available operations.
4. Once cancellation is listed, invoke it as
   `./bin/educationctl cancel --id ID --reason REASON`.

The `--reason` value is stored exactly as supplied. Shell quoting is therefore
required when it contains spaces.

Search and retrieval operations may be exposed during the first stage, but
they are unnecessary when the request already supplies a stable ID. There is
no general update operation in this sandbox.

Do not inspect `bin/educationctl`, `.protected/`, `data/`, or `var/`, and do not
modify any of them directly. Use the executable and rely on its actual
responses.
