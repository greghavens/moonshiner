# Local Travel Desk

`./travelctl` is the sole supported interface to the travel-record ledger in
this sandbox. Run `./travelctl --help` and the relevant subcommand help to
discover the live command-line interface.

The executable returns JSON on standard output and errors on standard error.
Retrievals return complete records. Status changes are compare-and-set
operations: a change succeeds only while the record still has the supplied
expected status.

Run independent operations as separate `travelctl` processes. Do not inspect
or edit the executable, `.travel`, or `.harness`; the client and verifier own
those files. Executing `travelctl` and the documented verifier is permitted.
