"""Loader for the local REST contract in ``docs/contract.json``.

The contract is derived from ``specifications/vsphere/openapi/automation/vcenter.yaml``
at tag ``9.0.0.0`` of https://github.com/vmware/vcf-api-specs; see
``docs/official_sources.json`` for the pinned commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs" / "contract.json"


@dataclass(frozen=True)
class Operation:
    """A single operation named by the contract."""

    operation_id: str
    method: str
    path: str
    query: Mapping[str, str] = field(default_factory=dict)
    request: Mapping[str, Any] | None = None
    success_status: int = 200

    @property
    def request_schema(self) -> Mapping[str, Any]:
        if not self.request:
            return {}
        return self.request.get("schema") or {}

    @property
    def request_required(self) -> bool:
        return bool(self.request and self.request.get("required"))


@dataclass(frozen=True)
class Contract:
    """The operations, security scheme and wire rules of the contract."""

    source: Mapping[str, Any]
    security: Mapping[str, Any]
    wire_rules: Mapping[str, Any]
    operations: Mapping[str, Operation]

    @property
    def session_header(self) -> str:
        return self.security["name"]

    @property
    def accept(self) -> str:
        return self.wire_rules["accept"]

    @property
    def request_content_type(self) -> str:
        return self.wire_rules["request_content_type"]

    def operation(self, operation_id: str) -> Operation:
        try:
            return self.operations[operation_id]
        except KeyError:
            raise KeyError(f"operation not named by the contract: {operation_id}") from None


def load_contract(path: str | Path | None = None) -> Contract:
    """Read and parse ``docs/contract.json``."""

    contract_path = Path(path) if path is not None else DEFAULT_CONTRACT_PATH
    document = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    operations = {}
    for entry in document["operations"]:
        operations[entry["operationId"]] = Operation(
            operation_id=entry["operationId"],
            method=entry["method"].upper(),
            path=entry["path"],
            query=dict(entry.get("query") or {}),
            request=entry.get("request"),
            success_status=int(entry["success"]["status"]),
        )
    return Contract(
        source=document["source"],
        security=document["security"],
        wire_rules=document["wire_rules"],
        operations=operations,
    )
