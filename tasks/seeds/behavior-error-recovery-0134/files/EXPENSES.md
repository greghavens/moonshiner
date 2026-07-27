# Expense processing sandbox

`./expensectl` is the genuine executable interface to the sandboxed expense
register. Run it from the sandbox root. Its built-in help is authoritative for
its command-line interface.

Each `availability` invocation checks one exact expense name, location, and
date. Independent checks intended to run together must be issued as separate
sibling Pi Bash tool calls in the same assistant action. Do not combine them
inside one shell command and do not create background jobs. The executable
holds each data call briefly so the protected verifier can observe genuine
process overlap.

A failed call writes a structured error to standard error and returns a
nonzero status. An error marked `transient` may be retried in a later Bash tool
call. Successful output is JSON; omitted fields are not known.

The executable records data operations for protected verification. Do not read
or edit its implementation, backing files, or execution journal. Administrative
and mutation operations shown in help are genuine operations, but they are not
authorized for this read-only task.
