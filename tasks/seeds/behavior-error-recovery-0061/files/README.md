# Calendar operations sandbox

`calendarctl` is the installed client for the sandboxed calendar service. It
starts the local service automatically and documents its interface through
`./calendarctl --help` and the subcommand help pages.

Use the client for all calendar reads and mutations. The `.calendar/` directory
is service-owned data and implementation; do not inspect or edit it directly.
The `.moonshiner/` directory contains protected verification code.
