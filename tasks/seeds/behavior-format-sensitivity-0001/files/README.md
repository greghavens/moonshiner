# Calendar queue operator notes

`calendarctl` is the installed command-line interface to this sandbox's
calendar queue and meeting store. Use its top-level `--help` output to discover
the live command syntax and the response contract.

The pending queue item is `queue/lookup.xml`. Calendar data and execution
evidence are private implementation state; access them only through
`calendarctl`.
