# Inventory record sandbox

`./inventory-records` is the only supported interface to the inventory ledger in
this workspace. It is a genuine executable backed by local persistent state. Do
not open or modify the program, state directory, audit trail, or `.harness`
contents directly.

Run `./inventory-records --help` to discover its operations. Run
`./inventory-records <operation> --help` to inspect the arguments accepted by a
particular operation. Each invocation performs exactly one ledger operation and
prints its real result as JSON. Independent operations can be started as sibling
Pi Bash calls when the task requests concurrency.

The `update` operation is guarded: it changes a record only when the record's
current status equals the caller-supplied expected status. Its JSON result says
whether the condition matched and whether a row was updated.
