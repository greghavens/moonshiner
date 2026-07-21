# configarchive

`configarchive` creates deterministic, content-addressed configuration snapshots.
It invokes a transport executable for each tab-separated `device<TAB>endpoint`
inventory entry. The bundled transport only reads local fixtures; it never opens
a network connection.

Run the sample inventory with:

```sh
CONFIGARCHIVE_FIXTURE_ROOT=fixtures \
  bin/configarchive inventory.tsv archive shims/local-device
```

Each device directory contains `latest.conf`, `latest.sha256`, immutable files in
`versions/`, and unified diffs in `changes/`. Known timestamp and clock-period
lines are removed before hashing. `run.report` is deterministic and contains no
wall-clock time.

All credentials in the fixtures are conspicuous lab-only placeholders. The tool
does not implement encryption or contact remote devices.
