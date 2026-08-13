"""Dependency-free SDDC Manager client for the operations in docs/contract.json."""

import json
import urllib.error
import urllib.parse
import urllib.request

from .contract import Contract
from .spec import build_token_spec, build_update_spec


class SddcApiError(Exception):
    def __init__(self, operation_id, status, payload):
        super().__init__("%s failed with HTTP %s" % (operation_id, status))
        self.operation_id = operation_id
        self.status = status
        self.payload = payload


class AuthenticationError(SddcApiError):
    """The presented credential or token was rejected."""


class SddcManagerClient:
    def __init__(self, base_url, contract=None, timeout=10.0):
        self.base_url = base_url.rstrip("/")
        self.contract = contract or Contract()
        self.timeout = timeout

    # -- transport ------------------------------------------------------------

    def _call(self, operation_id, path_params=None, query=None, body=None, token=None):
        path = self.contract.path_for(operation_id, **(path_params or {}))
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)

        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = "Bearer " + token

        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=self.contract.method(operation_id),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read().decode("utf-8")
            payload = _decode(raw)
            if status in (401, 403):
                raise AuthenticationError(operation_id, status, payload)
            raise SddcApiError(operation_id, status, payload)

        payload = _decode(raw)
        expected = self.contract.success_status(operation_id)
        if status != expected:
            raise SddcApiError(operation_id, status, payload)
        return payload

    # -- operations -----------------------------------------------------------

    def create_token(self, username, password):
        """createToken -> TokenPair.accessToken"""
        payload = self._call("createToken", body=build_token_spec(username, password))
        return payload["accessToken"]

    def list_credentials(self, token, **filters):
        """getCredentials -> PageOfCredential.elements"""
        query = {name: value for name, value in filters.items() if value is not None}
        payload = self._call("getCredentials", query=query, token=token)
        return payload.get("elements") or []

    def get_credential(self, credential_id, token):
        """getCredential -> Credential"""
        return self._call("getCredential", path_params={"id": credential_id}, token=token)

    def rotate_passwords(self, target, token, operation_type="ROTATE", password=None):
        """updateOrRotatePasswords -> Task (an acknowledgement, not a result)"""
        body = build_update_spec(target, operation_type, password=password)
        return self._call("updateOrRotatePasswords", body=body, token=token)

    def get_credentials_task(self, task_id, token):
        """getCredentialsTask -> CredentialsTask"""
        return self._call("getCredentialsTask", path_params={"id": task_id}, token=token)


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw
