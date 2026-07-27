# Expense registry

`./expensesctl` is the only supported interface to this sandboxed registry.
Run `./expensesctl --help` for its command syntax. Successful read operations
emit JSON to standard output. The registry and execution audit are managed by
the executable and must not be opened or changed directly.
