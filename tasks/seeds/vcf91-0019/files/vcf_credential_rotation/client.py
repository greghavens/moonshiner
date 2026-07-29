"""VCF 9.1 SDDC Manager managed-credential rotation."""

from __future__ import annotations

import time
from contextlib import AbstractContextManager
from typing import Any, Callable


class SddcManagerError(RuntimeError):
    """An SDDC Manager HTTP operation returned a non-success response."""


class ProtocolError(RuntimeError):
    """An SDDC Manager success response violated the pinned contract."""


class RotationFailedError(RuntimeError):
    """The credential task reached an unsuccessful terminal state."""


class RotationTimeoutError(RuntimeError):
    """The credential task exhausted its polling budget."""


class ManagedCredential:
    """A managed secret whose users acquire concurrency-safe leases."""

    def __init__(self, initial_secret: str) -> None:
        raise NotImplementedError("implement the managed credential gate")

    def lease(self) -> AbstractContextManager[str]:
        raise NotImplementedError("implement credential leasing")

    @property
    def is_rotating(self) -> bool:
        raise NotImplementedError("implement rotation state reporting")


class SddcManagerCredentialRotator:
    """Coordinate a VCF password update with local credential leases."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        sleep: Callable[[float], Any] = time.sleep,
        poll_interval: float = 1.0,
        max_polls: int = 60,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError("implement the contract-pinned client")

    def rotate(
        self,
        credential: ManagedCredential,
        *,
        resource_type: str,
        username: str,
        resource_id: str | None = None,
        resource_name: str | None = None,
        credential_type: str | None = None,
        account_type: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("implement drain, update, poll, and publish")
