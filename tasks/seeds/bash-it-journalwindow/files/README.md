# journalwindow

`journalwindow` makes a focused, reproducible `journalctl` query and renders its
JSON output without collapsing or prefixing message lines. It requires Bash 4+
and Python 3, and has no third-party dependencies.

```console
./journalwindow --unit api.service --severity warning --boot -1 \
  --since '2025-01-01 10:00:00' --until '2025-01-01 10:15:00' \
  --redact 'literal-secret'
```

The first output line is the exact Bash command used to query the journal. Each
entry then has a metadata line, its message exactly as decoded from journal
JSON (embedded newlines are retained), and a `--` separator. Redaction is a
literal replacement in all rendered journal fields; redaction tokens are not
query arguments and therefore do not appear in the reproduction command.

Within each boot, monotonic time establishes event order. A warning is emitted
if wall-clock time moves backward while monotonic time moves forward. Entries
from different boots are never compared.

For deterministic tests, `JOURNALWINDOW_JOURNALCTL` may name a journalctl-compatible
executable. Run the suite offline with:

```console
python3 -m unittest discover -s tests -p 'test_*.py'
```
