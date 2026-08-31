# NSX Policy partial-change reporter

This package is a deliberately incomplete VCF 9.1 integration exercise.

Implement the production code and source-traceability files described in the
task. Run the checks with:

```sh
go test -race ./...
```

`internal/protected` is verifier-owned and must not be changed.
