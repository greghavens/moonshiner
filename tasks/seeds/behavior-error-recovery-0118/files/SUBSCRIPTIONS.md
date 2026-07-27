# Subscription availability sandbox

`subscriptionctl` is the supported read-only interface to subscription
availability in this workspace. Run its built-in help for the authoritative
syntax:

```text
./subscriptionctl --help
./subscriptionctl availability --help
```

An availability request addresses one exact plan, group, and ISO date. The
command writes one JSON object to standard output and exits zero when it finds
a complete availability result. An error is written to standard error and
exits nonzero. Retry an error only when its JSON explicitly sets both
`transient` and `retryable` to `true`.

Each invocation is recorded by the executable. Do not inspect or change its
database, runtime directory, or execution evidence.
