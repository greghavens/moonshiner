# Local support-case operations system

`supportctl` is the command-line client for the sandboxed support-case
operations system. Run its built-in help to discover the supported operations
and use the client for every support-case data action. Each invocation performs
one operation.

The client can report a response deadline after a request has already reached
the service. Such a timeout does not reveal whether a mutation committed. Use a
fresh exact-ID retrieval when the current record is needed to resolve that
uncertainty.

The client, service internals, runtime data, and protected files are task
fixtures. Do not inspect or edit them, and do not create operation evidence by
hand.
