# Sandboxed program board

Use the executable in this workspace for every program-data operation. Start
with its built-in help:

    ./program-board --help

Availability results and errors are JSON. A failed availability response may
be retried only when its JSON explicitly marks the failure as both transient
and retryable.

The program board owns its backing records and signed execution journal. Use
the executable rather than reading or editing those implementation files.
