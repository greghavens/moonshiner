# Reservation administration sandbox

`./reservationctl` is the executable interface to this sandbox's reservation
registry. Run it from the sandbox root. Its built-in help is authoritative for
command syntax.

The client operates on durable local reservation state. A cancellation can be
committed by the registry before the client reaches its acknowledgement
deadline. A deadline error therefore leaves the outcome unknown. Retrieve the
full reservation directly by its stable ID before deciding whether a retry is
needed.

Every reservation operation appends authenticated audit evidence. Do not
inspect or edit the runtime directory, state, journal, protected seed, audit
key, setup, or verifier. Only operations explicitly allowed by the task are in
scope.
