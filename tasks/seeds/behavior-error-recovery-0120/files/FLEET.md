# Fleet availability sandbox

`fleetctl` is the supported interface to fleet data in this workspace. Run its
built-in help for the authoritative syntax and available operations:

```text
./fleetctl --help
./fleetctl availability --help
```

An availability request addresses one exact vehicle, depot, and ISO date. The
command writes one JSON object to standard output and exits zero when it finds
a complete availability result. An error is written to standard error and
exits nonzero. Retry an error only when its JSON explicitly sets both
`transient` and `retryable` to `true`.

Each data operation is recorded by the executable. Do not inspect or change
its database, runtime directory, or execution evidence.
