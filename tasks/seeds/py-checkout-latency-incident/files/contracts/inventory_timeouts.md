# Checkout dependency timeout contract

Checkout owns the end-to-end request budget. A dependency may use less of that
budget but may never run beyond it.

The inventory gateway owns two separate limits:

* response headers for `reserve`: 150 ms, capped by the remaining request budget;
* optional failure-detail body collection: 75 ms, capped by the remaining
  request budget.

Failure-detail collection is best effort. Once response headers establish an
inventory failure, inability to read its optional body must not replace the
known status or `X-Request-Id`. The body-read error remains available as the
cause of the inventory failure.

When failure-detail collection times out, emit
`inventory.failure_detail_timeout`. It carries `checkout_id`, `status`, `request_id`,
`configured_timeout_ms`, `remaining_budget_ms`, `effective_timeout_ms`,
`timeout_owner`, `error_type`, `operation`, and `error`. `timeout_owner` is
`inventory_failure_diagnostics` when the 75 ms cap is active and
`checkout_budget` when the remaining parent budget is smaller.
