# Sandboxed message register

`registerctl` is the executable client for this workspace's message register.
Run `./registerctl --help` to inspect its current command interface and run
`./registerctl <command> --help` for a command's options.

Search results are discovery summaries. They are not complete records and must
not be used as a substitute for a full-record retrieval. The register contains
similarly named, similarly located, archived, pending, and unrelated entries,
so use every search constraint supplied by the request and follow a unique
returned stable ID.

The client maintains its own execution evidence. Do not inspect, edit, or
manufacture that evidence. Access register data only through the executable.
