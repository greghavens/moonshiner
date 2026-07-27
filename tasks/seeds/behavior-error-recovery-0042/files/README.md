# Sandboxed travel availability

`./visitctl` is the executable interface to this sandbox's travel availability
register. Run `./visitctl --help` to learn its command-line interface.

A completed lookup writes one JSON object to standard output. An operational
failure writes a JSON error to standard error and exits nonzero. When such an
error explicitly identifies itself as retryable, retry the same request rather
than broadening or substituting it.

The register is read-only through this client. Do not inspect the executable,
the runtime directory, or protected files to obtain availability data.
