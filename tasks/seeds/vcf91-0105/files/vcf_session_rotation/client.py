"""Stdlib-only client for the focused vCenter session-rotation contract."""

from typing import Any, Sequence


CREATE_SESSION_OPERATION = "Cis.Session_create"
LIST_VMS_OPERATION = "Vcenter.VM_list"
DELETE_SESSION_OPERATION = "Cis.Session_delete"


class VcenterError(RuntimeError):
    """An HTTP or transport failure."""


class ProtocolError(RuntimeError):
    """A malformed successful response."""


class RotatingVcenterClient:
    """Use generation-pinned sessions and drain old work during rotation."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError("Implement the contract-backed client.")

    def list_vms(
        self,
        *,
        vms: Sequence[str] | None = None,
        names: Sequence[str] | None = None,
        folders: Sequence[str] | None = None,
        datacenters: Sequence[str] | None = None,
        hosts: Sequence[str] | None = None,
        clusters: Sequence[str] | None = None,
        resource_pools: Sequence[str] | None = None,
        power_states: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("List VMs through the captured session.")

    def rotate_password(self, new_password: str) -> None:
        raise NotImplementedError("Publish, drain, and retire a session generation.")

    def close(self) -> None:
        raise NotImplementedError("Drain and close the active session.")

    def __enter__(self) -> "RotatingVcenterClient":
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.close()
