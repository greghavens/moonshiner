# Support sandbox

`supportctl` is the command-line client for the sandboxed support-case service.
Run `./supportctl --help` and the relevant subcommand help to learn its current
interface. Case operations accept stable IDs; no title lookup is necessary.

Each invocation performs at most one support operation. A client deadline can
expire after the service has accepted and committed a request. In that case the
client reports an uncertain outcome and exits nonzero. Resolve uncertainty with
an exact-ID read before deciding whether a retry is safe.

The database and signed operation audit are service-managed. Do not inspect or
edit runtime or protected files directly.
