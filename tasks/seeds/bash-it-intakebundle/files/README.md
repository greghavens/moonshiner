# intake-bundle

`bin/intake-bundle` creates an offline support archive from a fixed allowlist
beneath one source directory. It collects the application log, an optional
worker log, captured Bash-it version output, captured disk facts, and a redacted
environment file. It never searches the source tree and never invokes network
tools.

Run the included example with GNU Bash, GNU coreutils, GNU tar, and gzip:

```sh
bin/intake-bundle --source fixtures --output /tmp/intake-bundle.tar.gz
tar -tzf /tmp/intake-bundle.tar.gz
tar -xOzf /tmp/intake-bundle.tar.gz ./manifest.tsv
```

The archive contains evidence under `evidence/` plus `manifest.tsv`. Manifest
rows are tab-separated `status`, `kind`, `path`, and `digest_or_reason` fields.
A collected row hashes the bytes stored in the archive; a missing optional file
has reason `missing_optional`. Source paths in the manifest are relative, so
temporary or host directory names are not disclosed.

Sensitive shell-style assignments in `config/bash-it.env` are replaced with
`[REDACTED]`. Archive entry order, timestamps, ownership, modes, and gzip
headers are normalized so identical evidence produces identical archive bytes.
Publication is atomic and a collection error leaves an existing output alone.
