# Local Customer Support Ledger

`./supportctl` is the sole supported interface to the support-case ledger in
this sandbox. Invoke its built-in help and the relevant subcommand help to
discover the live command-line interface.

The executable returns JSON on standard output and errors on standard error.
Retrievals return complete records. Status changes are compare-and-set
operations: a change succeeds only while the record still has the supplied
expected status.

Run independent operations as separate `supportctl` processes. Do not inspect
or edit the executable, `.support`, or `.protected`; the client and verifier
own those files. Executing `supportctl` and the documented verifier is
permitted.
