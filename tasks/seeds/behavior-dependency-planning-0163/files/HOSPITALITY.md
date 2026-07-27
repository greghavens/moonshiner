# Local Hospitality Ledger

`./hospitalityctl` is the sole supported interface to the hospitality-record
ledger in this sandbox. Run `./hospitalityctl --help` and the relevant
subcommand help to discover the live command-line interface.

The executable returns JSON on standard output and errors on standard error.
Retrievals return complete records. Status changes are compare-and-set
operations: a change succeeds only while the record still has the supplied
expected status.

Run independent operations as separate `hospitalityctl` processes. Do not
inspect or edit the executable, `.hospitality`, or `.protected`; the client and
verifier own those files. Executing `hospitalityctl` and the documented
verifier is permitted.
