"""Deskline helpdesk API client.

Started in 2023 as a 20-line helper for the billing bot; five teams and one
mobile app later it is the only way anything in the company talks to
Deskline. Transport, auth, response decoding, and every domain call live in
this one class -- which is why nobody can unit-test a resource method
without standing up a live server.
"""
import json
import urllib.error
import urllib.request


class ApiError(Exception):
    """A Deskline error response, decoded from their error envelope."""

    def __init__(self, code, message, status):
        super().__init__("%s: %s (HTTP %d)" % (code, message, status))
        self.code = code
        self.message = message
        self.status = status


class AuthError(ApiError):
    """A 401 -- the key is wrong, revoked, or missing."""


class DesklineClient:
    """Everything-in-one client for the Deskline REST API."""

    def __init__(self, base_url, api_key, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _call(self, method, path, payload=None):
        # transport + auth + serialization, all in one place (sorry)
        url = self.base_url + path
        headers = {"X-Api-Key": self.api_key, "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            raw = e.read()
        body = json.loads(raw.decode("utf-8")) if raw else None
        if status >= 400:
            err = (body or {}).get("error", {})
            code = err.get("code", "error")
            message = err.get("message", "")
            if status == 401:
                raise AuthError(code, message, status)
            raise ApiError(code, message, status)
        return status, body

    # ------------------------------------------------------------ tickets

    def get_ticket(self, ticket_id):
        """Fetch one ticket; None when Deskline has no such ticket."""
        try:
            _, body = self._call("GET", "/tickets/%s" % ticket_id)
        except ApiError as e:
            if e.status == 404:
                return None
            raise
        return body

    def create_ticket(self, subject, body, priority="normal"):
        if not subject or not subject.strip():
            raise ValueError("subject must not be empty")
        if priority not in ("low", "normal", "high"):
            raise ValueError("unknown priority: %r" % (priority,))
        _, created = self._call(
            "POST", "/tickets",
            {"subject": subject, "body": body, "priority": priority},
        )
        return created

    def close_ticket(self, ticket_id, resolution):
        _, body = self._call(
            "POST", "/tickets/%s/close" % ticket_id, {"resolution": resolution}
        )
        return body

    def list_open_tickets(self):
        _, body = self._call("GET", "/tickets?state=open")
        return (body or {}).get("tickets", [])
