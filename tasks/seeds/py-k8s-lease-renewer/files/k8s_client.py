"""Minimal Kubernetes API client for the coordination.k8s.io/v1 Lease API.

Plain JSON over HTTP with a bearer token, stdlib only. Deliberately tiny:
our scheduler sidecars need lease read/create/replace plus honest error
reporting, nothing else.
"""

import json
import urllib.error
import urllib.request

LEASE_COLLECTION = "/apis/coordination.k8s.io/v1/namespaces/{namespace}/leases"


class ApiError(Exception):
    """A Kubernetes API failure decoded from a meta/v1 Status body."""

    def __init__(self, status_code, reason, message):
        super().__init__(f"{status_code} {reason}: {message}")
        self.status_code = status_code
        self.reason = reason
        self.message = message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: our bearer token must not leave the server we
    were configured with, so any 3xx is surfaced to the caller instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ApiClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect())

    def request(self, method, path, body=None):
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers=headers, method=method)
        try:
            with self._opener.open(req) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as err:
            raise self._to_api_error(err) from None
        return json.loads(payload) if payload else None

    @staticmethod
    def _to_api_error(err):
        if 300 <= err.code < 400:
            return ApiError(err.code, "Redirect",
                            "refusing to follow a redirect issued by the API server")
        try:
            status = json.loads(err.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            status = {}
        return ApiError(status.get("code", err.code),
                        status.get("reason", "Unknown"),
                        status.get("message", "(no Status body)"))

    # ------------------------------------------------------------ leases

    def get_lease(self, namespace, name):
        return self.request("GET", LEASE_COLLECTION.format(namespace=namespace) + "/" + name)

    def create_lease(self, namespace, body):
        return self.request("POST", LEASE_COLLECTION.format(namespace=namespace), body)

    def replace_lease(self, namespace, name, body):
        return self.request("PUT", LEASE_COLLECTION.format(namespace=namespace) + "/" + name, body)
