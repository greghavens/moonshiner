"""Core service for the firmware release package broker.

The API layer has already authenticated each ``RequestContext``.  This module
owns resource authorization and denial auditing.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Mapping, Optional


class NotFound(Exception):
    """The requested resource is absent or intentionally concealed."""


class Forbidden(Exception):
    """The caller is not allowed to access the requested tenant."""


@dataclass(frozen=True)
class RequestContext:
    actor_id: str
    tenant_id: str


@dataclass(frozen=True)
class Package:
    package_id: str
    tenant_id: str
    owner_id: str
    contents: bytes


@dataclass(frozen=True)
class Device:
    device_id: str
    tenant_id: str
    status: str


class MemoryRepository:
    def __init__(
        self,
        packages: tuple[Package, ...] = (),
        devices: tuple[Device, ...] = (),
    ) -> None:
        self._packages = {item.package_id: item for item in packages}
        self._devices = {item.device_id: item for item in devices}

    def find_package(self, package_id: str) -> Optional[Package]:
        return self._packages.get(package_id)

    def find_device(self, device_id: str) -> Optional[Device]:
        return self._devices.get(device_id)


class AuditLog:
    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []

    def denial(
        self,
        *,
        reason: str,
        actor_id: str,
        tenant_id: str,
        resource_id: str,
    ) -> None:
        self.events.append(
            {
                "event": "access_denied",
                "reason": reason,
                "actor_id": actor_id,
                "tenant_id": tenant_id,
                "resource_id": resource_id,
            }
        )


class PackageService:
    def __init__(
        self,
        repository: MemoryRepository,
        audit: AuditLog,
        config: Mapping[str, Any],
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._audit_denials = config.get("audit_denials") is True

    def get_package(self, context: RequestContext, package_id: str) -> bytes:
        package = self._repository.find_package(package_id)
        if package is None:
            raise NotFound(package_id)

        if package.tenant_id != context.tenant_id:
            self._record_denial("tenant_mismatch", context, package_id)
            raise Forbidden(package_id)

        # Package IDs are opaque and tenant scoped, so the tenant boundary was
        # historically considered sufficient here.
        return package.contents

    def get_device_status(
        self,
        context: RequestContext,
        device_id: str,
    ) -> str:
        device = self._repository.find_device(device_id)
        if device is None:
            raise NotFound(device_id)

        if not hmac.compare_digest(device.tenant_id, context.tenant_id):
            self._record_denial("tenant_mismatch", context, device_id)
            raise Forbidden(device_id)

        return device.status

    def _record_denial(
        self,
        reason: str,
        context: RequestContext,
        resource_id: str,
    ) -> None:
        if self._audit_denials:
            self._audit.denial(
                reason=reason,
                actor_id=context.actor_id,
                tenant_id=context.tenant_id,
                resource_id=resource_id,
            )
