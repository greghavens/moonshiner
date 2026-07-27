# Support case registry sandbox

`./case-registry` is the executable client for this sandbox's durable support
case service. Run it from the sandbox root. Its built-in help and subcommand
help are authoritative for the command-line interface.

Use immutable case IDs for targeted work. The client enforces a response
deadline. If that deadline expires after a cancellation request reaches the
service, the response does not establish whether the requested write
committed. Resolve an uncertain cancellation by fetching that same immutable
ID before deciding whether an identical retry is necessary.

The client owns its SQLite runtime database and signed operation journal. Do
not read or change those implementation files directly.

After completing the requested workflow and writing its deliverable, run the
protected check with:

```text
python3 -B .protected/verify.py
```
