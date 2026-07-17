# stockroom

Warehouse service behind the fulfillment tooling: stock levels per
warehouse, restock/pick movements with an audit trail, an availability
report for the ops dashboard, and a nightly reconciliation job that
compares stored quantities against the movement history and writes a
ledger entry per warehouse.

## Layout

    service.go   root wiring: New(cfg, clock) -> Service (handler + scheduler)
    config.go    service configuration and defaults
    api/         HTTP layer (net/http mux, JSON handlers, error mapping)
    domain/      pure business logic and shared types/errors
    store/       in-memory persistence: stock rows, movements, ledger, locks
    jobs/        the job scheduler and the reconciliation job

## Conventions

- Stock rows for a warehouse are kept ordered by SKU; lookups binary-search.
- Every stock change goes through a movement (restock/pick) so the ledger
  can be reconciled from the audit trail.
- A warehouse lock marks a counting session in progress: reconciliation
  waits (retries shortly) instead of reporting against a moving target.

## Tests

    go test -race -timeout 120s ./...
