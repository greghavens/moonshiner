"""Customer name expand/backfill/contract migration example."""

from .migration import (
    BatchResult,
    MIGRATION_NAME,
    backfill_batch,
    capture_rollback_fixture,
    connect_memory,
    contract_schema,
    create_legacy_schema,
    expand_schema,
    load_legacy_fixture,
    migration_status,
    rollback_to_legacy,
)
from .repository import Customer, CustomerRepository

__all__ = [
    "BatchResult",
    "Customer",
    "CustomerRepository",
    "MIGRATION_NAME",
    "backfill_batch",
    "capture_rollback_fixture",
    "connect_memory",
    "contract_schema",
    "create_legacy_schema",
    "expand_schema",
    "load_legacy_fixture",
    "migration_status",
    "rollback_to_legacy",
]
