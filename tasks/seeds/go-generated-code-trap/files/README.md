# Catalog sync client

`syncer.SyncAll` walks the catalog service's paginated `ListWidgets`
operation. The HTTP client under `internal/catalogapi` is checked in so normal
builds do not need generator tooling.

The client is generated from `api/catalog.json`:

```sh
go generate ./...
```

Do not hand-edit files bearing the generated-code header. Update their owning
contract and regenerate instead.

Run the complete local verification with:

```sh
python3 -B .moonshiner/verify.py
```
