# Course availability sandbox

`course-availability` is the executable interface to the local course catalog.
Run it from this directory. A successful check emits JSON to standard output.
A failed check emits a JSON error to standard error and exits nonzero.

Discover the executable's current command-line interface with its built-in
help. Successful output contains one `availability` string. Error output
contains a boolean `transient` field. Only that field determines whether a
failed check may be retried; do not infer retry eligibility from wording or an
exit status.

The catalog supports concurrent reads. Pi can issue sibling Bash tool calls
from one assistant action concurrently. Put each independent check in its own
direct Bash call. Do not combine checks in a shell command or create background
jobs inside a Bash call.

Every check is journaled automatically for protected verification. Do not read
or change `.catalog/`. Except for the task prompt's exact final verifier command,
do not inspect, modify, or execute anything under `.harness/`, including
generated runtime files. The executable, backing data, and runtime journal are
protected environment internals.
