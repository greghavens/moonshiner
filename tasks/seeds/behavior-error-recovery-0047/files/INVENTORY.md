# Inventory service sandbox

`./inventory-service` is the executable client for this sandbox's durable
inventory service. Run it from the sandbox root. Its built-in help and
subcommand help are authoritative for the command-line interface.

Use immutable item IDs for targeted work. The client enforces a response
deadline. If a response is lost after a cancellation reaches the service, the
error does not establish whether the business write committed. Resolve that
uncertainty by fetching the same immutable ID before deciding whether an
identical retry is necessary.

The executable owns its SQLite runtime database and signed operation journal.
Do not read or change those implementation files directly.

After completing the requested workflow and writing its deliverable, run the
protected check with:

```text
python3 -B .protected/verify.py
```
