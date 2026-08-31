"""HTTP layer for three VCF Operations 9.1 operations.

The implementation is intentionally incomplete. Research the official VMware API
contract before filling in its methods, paths, authorization, and request bodies.
"""

import json
import urllib.error
import urllib.parse
import urllib.request


class VcfOperationsError(Exception):
    """Any non-success answer from the VCF Operations API."""

    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class AuthenticationFailed(VcfOperationsError):
    """acquireToken was rejected: the credentials or auth source are wrong."""


class TokenExpired(VcfOperationsError):
    """An authenticated operation was answered 401: the access token is no longer live."""


def build_acquire_body(username, password, auth_source=None):
    """Body for acquireToken (schema `username-password`).

    `username` and `password` are required; `authSource` is optional.
    """
    return {
        "username": username,
        "password": password,
        "authSource": auth_source or "",
    }


# Mapping from this package's filter keyword arguments to `alert-query` property names.
ALERT_QUERY_FIELDS = (
    ("active_only", "activeOnly"),
    ("criticality", "alertCriticality"),
    ("alert_status", "alertStatus"),
    ("alert_name", "alertName"),
    ("resource_kind", "resourceKind"),
)


def build_alert_query(filters):
    """Body for queryAlert (schema `alert-query`).

    Every property of `alert-query` is optional; `filters` carries only the ones the
    caller actually asked for, keyed by the left-hand names in ALERT_QUERY_FIELDS.
    """
    unknown = set(filters) - {local for local, _ in ALERT_QUERY_FIELDS}
    if unknown:
        raise ValueError("unsupported alert filters: %s" % sorted(unknown))
    return {
        "activeOnly": filters.get("active_only"),
        "alertCriticality": filters.get("criticality") or [],
        "alertStatus": filters.get("alert_status") or [],
        "alertName": filters.get("alert_name") or "",
        "resourceKind": filters.get("resource_kind") or "",
    }


class VcfOperationsClient:
    """Talks to one VCF Operations instance using a token from acquireToken."""

    def __init__(
        self,
        base_url,
        username,
        password,
        auth_source=None,
        contract=None,
        timeout=15.0,
    ):
        self.contract = contract
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._username = username
        self._password = password
        self._auth_source = auth_source
        self._token = None
        self.token_acquisitions = 0
        self._operations = {}
        self._auth_header = None
        self._auth_value_format = None

    # -- token ------------------------------------------------------------

    @property
    def token(self):
        return self._token

    def acquire_token(self):
        """Run acquireToken and adopt the returned access token."""
        body = build_acquire_body(self._username, self._password, self._auth_source)
        payload = self._send(
            "acquireToken",
            self._operations["acquireToken"]["path"],
            body=body,
            authenticated=False,
        )
        self._token = payload["token"]
        self.token_acquisitions += 1
        return self._token

    # -- operations -------------------------------------------------------

    def query_alerts(self, page, page_size, filters=None):
        """queryAlert: one page of the alert set matching `filters`."""
        return self._send(
            "queryAlert",
            self._operations["queryAlert"]["path"],
            query={"page": page, "pageSize": page_size},
            body=build_alert_query(filters or {}),
        )

    def get_alert(self, alert_id):
        """getAlert: the full alert record behind a summary from queryAlert."""
        path = self._operations["getAlert"]["path"].replace(
            "{id}", urllib.parse.quote(str(alert_id), safe="")
        )
        return self._send("getAlert", path)

    # -- plumbing ---------------------------------------------------------

    def _url(self, path, query=None):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def _send(self, operation_id, path, query=None, body=None, authenticated=True):
        url = self._url(path, query)
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            if not self._token:
                raise TokenExpired("%s needs a token; none has been acquired" % operation_id)
            headers[self._auth_header] = self._auth_value_format.format(token=self._token)

        method = self._operations[operation_id]["method"]
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw) if raw else None
            except ValueError:
                payload = {"message": raw}
            if exc.code == 401:
                if authenticated:
                    raise TokenExpired(
                        "%s was answered 401: the access token is no longer valid" % operation_id,
                        status=401,
                        payload=payload,
                    ) from exc
                raise AuthenticationFailed(
                    "acquireToken was rejected", status=401, payload=payload
                ) from exc
            raise VcfOperationsError(
                "%s failed with HTTP %d: %s" % (operation_id, exc.code, payload),
                status=exc.code,
                payload=payload,
            ) from exc
