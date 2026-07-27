# Recruiting administration sandbox

`recruitingctl` is the executable interface to the sandboxed recruiting
service. Run it from this directory. It stores live service state locally and
returns JSON when a request receives a response.

Discover the command-line interface with:

```text
./recruitingctl --help
```

The operations relevant to cancellation recovery are:

```text
./recruitingctl cancel --id STABLE_ID --reason REASON
./recruitingctl get --id STABLE_ID
```

`get` retrieves one complete candidate record by its exact stable ID. A
deadline error from `cancel` can occur after submission, so it does not by
itself reveal whether the service committed the cancellation. Resolve that
uncertainty by retrieving the same stable ID before deciding whether a repeat
request is safe.

The executable also offers other administrative operations for unrelated
work. Every data operation is recorded automatically for protected
verification. Do not read or change `.recruiting` or `.protected`; use the
executable as the only recruiting-data interface.
