# Calendar administration sandbox

`./calendarctl` is the executable client for this sandbox's calendar service.
Run it from the sandbox root. Its built-in help and the help for each
subcommand are authoritative for the command-line interface.

Use immutable meeting IDs for targeted work. The client enforces a response
deadline. A deadline error after a request reached the service gives no
reliable indication of whether the write committed. Resolve an uncertain
close with a direct read of that same ID before deciding whether an identical
retry is needed.

The client owns its SQLite runtime database and signed operation journal. Do
not read or change those implementation files directly.
