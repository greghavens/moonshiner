# Candidate records sandbox

`candidate-records` is the executable interface to the sandboxed recruiting
registry. Run it from this directory and use its built-in help for the current
command syntax. Successful operations emit one JSON object on standard output.
Operational failures emit one JSON object on standard error and return a
nonzero status.

A cancellation response can be lost after submission. A response timeout
therefore does not reveal whether the requested change committed. Retrieve the
exact record by its stable ID before deciding whether an identical retry is
safe.

Every candidate operation is recorded for protected verification. Do not read
or modify `candidate-records`, `.protected`, `.candidate-runtime`, or anything
inside those paths. The task authorizes only the operations and stable ID named
in the user request.
