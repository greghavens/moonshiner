"""HTTP transport for the four migration-upgrade operations in the contract."""

from __future__ import annotations

import json
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

from .contract import Contract, Operation, load_contract
from .errors import ApiError

GET_INIT_SPEC = "Vcenter.Lcm.Deployment.MigrationUpgrade_get"
APPLY = "Vcenter.Lcm.Deployment.MigrationUpgrade_apply"
GET_STATUS = "Vcenter.Lcm.Deployment.MigrationUpgrade.Status_get"
CANCEL = "Vcenter.Lcm.Deployment.MigrationUpgrade_cancel"


class MigrationUpgradeClient:
    """Issues the contract operations against one vCenter appliance."""

    def __init__(
        self,
        base_url: str,
        session_id: str,
        contract: Contract | None = None,
        *,
        opener: Any | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.contract = contract if contract is not None else load_contract()
        self.timeout = timeout
        self._opener = opener if opener is not None else urllib.request.build_opener()

    # -- transport ---------------------------------------------------------

    def _url(self, operation: Operation) -> str:
        url = self.base_url + operation.path
        if operation.query:
            url += "?" + urllib.parse.urlencode(operation.query)
        return url

    def _request(
        self, operation_id: str, body: Mapping[str, Any] | None = None
    ) -> tuple[int, Any]:
        operation = self.contract.operation(operation_id)
        headers = {
            "Accept": self.contract.accept,
            "Content-Type": self.contract.request_content_type,
            self.contract.session_header: self.session_id,
        }
        data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")

        request = urllib.request.Request(
            self._url(operation), data=data, headers=headers, method=operation.method
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = _decode(response.read())
                return response.status, payload
        except urllib.error.HTTPError as exc:
            raise _api_error(exc, operation_id) from None

    # -- operations --------------------------------------------------------

    def get_init_spec(self) -> Mapping[str, Any]:
        """``Vcenter.Lcm.Deployment.MigrationUpgrade_get``."""

        return self._request(GET_INIT_SPEC)[1]

    def apply(
        self, *, pause: str | None = None, start_switchover: str | None = None
    ) -> None:
        """``Vcenter.Lcm.Deployment.MigrationUpgrade_apply``."""

        spec: dict[str, Any] = {
            "pause": pause,
            "start_switchover": start_switchover,
        }
        self._request(APPLY, body=spec)

    def get_status(self) -> Mapping[str, Any]:
        """``Vcenter.Lcm.Deployment.MigrationUpgrade.Status_get``."""

        return self._request(GET_STATUS)[1]

    def cancel(self) -> None:
        """``Vcenter.Lcm.Deployment.MigrationUpgrade_cancel``."""

        self._request(CANCEL)


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _api_error(exc: urllib.error.HTTPError, operation_id: str) -> ApiError:
    try:
        envelope = _decode(exc.read()) or {}
    except (ValueError, OSError):
        envelope = {}
    if not isinstance(envelope, dict):
        envelope = {}
    messages = [
        message.get("default_message", "")
        for message in envelope.get("messages", [])
        if isinstance(message, dict)
    ]
    return ApiError(exc.code, operation_id, envelope.get("error_type"), messages)
