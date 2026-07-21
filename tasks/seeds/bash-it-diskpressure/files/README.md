# Deterministic disk-pressure incident

`bin/diskpressure` diagnoses a captured workstation filesystem and performs a
strictly allowlisted cache cleanup. It never runs the host's `df`, `du`, or
`rm`. Instead, `bin/diskfixture` exposes stable block, inode, usage, log, and
deletion behavior below a scenario directory.

Inspect the two incidents without changing them:

```sh
bash bin/diskpressure diagnose fixtures/block-pressure
bash bin/diskpressure diagnose fixtures/inode-pressure
```

The first incident is short on blocks; the second is short on inodes. A
remediation operates on a writable copy of a fixture and writes a two-column
TSV audit (`path`, `bytes`) for every removed cache entry. The regression test
creates those copies itself:

```sh
bash tests/run.sh
```

All commands are offline and deterministic.
