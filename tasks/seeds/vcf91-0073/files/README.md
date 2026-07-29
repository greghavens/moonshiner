# NSX Policy partial-change reporter

This package is a deliberately incomplete VCF 9.1 integration exercise.

Implement the production code and the two source-traceability JSON files described
in the task. The acceptance suite starts only loopback HTTP servers and runs with:

```sh
go test -race ./...
```

`internal/protected` is verifier-owned and must not be changed.
