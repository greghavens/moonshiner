# Project sandbox

`projectctl` is the executable interface to the local project registry. Run it
from this directory and use its built-in help to discover the available
operations and availability error semantics.

Project operations emit JSON to standard output. Errors use standard error and
a nonzero exit status. A retryable transient availability error is read-only
and explicitly identifies its retry properties. Independent checks can be run
concurrently.

Every project operation and top-level help discovery is recorded automatically
for protected verification. Do not read or modify `.projects`, `.protected`,
the executable implementation, backing state, runtime state, or execution
journal. Do not replace executable calls with direct file edits.
