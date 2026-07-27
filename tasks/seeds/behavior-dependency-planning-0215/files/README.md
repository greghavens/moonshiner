# Clinic appointment registry sandbox

`clinic-records` is the executable interface to the appointment registry in
this sandbox. Run it from this directory. Its built-in help documents every
available operation and argument. Search results are summaries; `get` returns
the complete record needed for an audit.

The registry supports concurrent reads. Pi starts sibling Bash tool calls from
one assistant message concurrently, so each independent operation should be in
its own tool call. Two commands combined in one shell call, including commands
started with shell backgrounding, are not sibling Pi tool calls.

Every registry operation records protected execution evidence automatically.
Use the executable as the only appointment interface. The `.clinic` and
`.harness` directories are implementation and verification data, not user-facing
interfaces.
