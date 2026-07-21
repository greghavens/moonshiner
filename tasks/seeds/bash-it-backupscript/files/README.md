# local-backup

`backup.sh` creates verifiable local snapshots using only Bash and GNU
coreutils. It never reads a source unless its resolved location is below a
root listed in the allowlist.

The allowlist is a text file containing one absolute directory per line.
Blank lines and lines whose first non-space character is `#` are ignored.
Set `BACKUP_ALLOWLIST` to its path.

```bash
BACKUP_ALLOWLIST=/etc/local-backup.allow \
  ./backup.sh backup /srv/backups /home/alice/Documents /etc/hosts
```

Each successful run publishes `DEST/snapshots/TIMESTAMP`, then atomically
updates `DEST/latest`. A snapshot contains a `payload/` tree rooted at the
original absolute paths, `manifest.tsv`, and `manifest.sha256`. Unchanged
regular files are hard-linked from the preceding snapshot. `BACKUP_KEEP`
controls retention and defaults to 5. A `DEST/.backup.lock` directory prevents
concurrent writers.

After publication the command prints a shell-escaped `Restore with:` command.
It names the exact snapshot, so retention or a later `latest` update cannot
silently change what is restored. Set `BACKUP_RESTORE_ROOT` before backup to
choose the destination shown by that instruction. Otherwise the suggested
destination is `$PWD/restore-TIMESTAMP`.

Restore verifies the manifest checksum and every payload checksum before it
copies anything:

```bash
./backup.sh restore /srv/backups/snapshots/20250101T120000Z /tmp/recovered
```

The original `/home/alice/Documents/report.txt` is restored as
`/tmp/recovered/home/alice/Documents/report.txt`. `verify` performs the same
integrity checks without restoring:

```bash
./backup.sh verify /srv/backups/snapshots/20250101T120000Z
```

For reproducible automation, `BACKUP_TIMESTAMP` may be set to a UTC snapshot
identifier in `YYYYmmddTHHMMSSZ` form. The command rejects unsupported special
files and path names containing tabs or newlines. Any validation, copy, or
manifest failure removes the staging tree and leaves the previously published
snapshot untouched.
