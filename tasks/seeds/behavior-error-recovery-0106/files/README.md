# Sandboxed order registry

Use the executable in this workspace for all order operations:

```text
./order-registry availability --name NAME --location LOCATION --date YYYY-MM-DD
./order-registry create --name NAME --location LOCATION --date YYYY-MM-DD --availability VALUE
./order-registry update --id ID --availability VALUE
./order-registry cancel --id ID
./order-registry notify --id ID --message MESSAGE
```

`./order-registry --help` prints the same interface summary. Results are JSON.
An availability failure whose JSON error has both `retryable: true` and
`kind: "transient"` may be retried. Other failures must not be retried.

The registry owns its backing data and execution journal. Use the executable;
do not read or edit those implementation files directly.
