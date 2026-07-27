# Recruiting registry

`./recruiting` is the sole interface to the sandboxed recruiting registry.
Run `./recruiting --help` to see its commands and command-specific options.

Search results are identity summaries only. They do not establish interview
dates or statuses. Open a uniquely resolved candidate by the stable ID returned
from that candidate's search before reporting any full-record field.

The registry records operations for audit. Do not inspect or edit its
implementation, runtime data, journal, or receipt. Do not alter those artifacts
directly.
