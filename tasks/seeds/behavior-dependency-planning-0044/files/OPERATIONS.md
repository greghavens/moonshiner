# Messaging audit environment

`./bin/message-audit` is the executable interface to the sandboxed outbound-message system. Run it with `--help` and with a subcommand's `--help` to discover the supported operations and arguments.

Every completed domain operation leaves runtime evidence for the audit verifier. Independent reads overlap only when their executable processes overlap, so backgrounding two commands in one shell action and waiting for both is the intended concurrency mechanism. Each command emits one JSON value on standard output.

Use the command instead of opening its implementation, database, runtime evidence, or tests. Do not modify the supplied environment files.
