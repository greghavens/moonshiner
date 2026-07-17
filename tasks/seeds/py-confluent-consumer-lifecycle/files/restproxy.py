"""Tiny HTTP transport for one Confluent REST Proxy v2 endpoint.

Deliberately small: base-URL handling, the v2 media types, Basic auth, the
v2 error envelope, and a hard no-redirect policy so credentials can never
trail off to a host we did not configure. Higher layers decide what a
redirect response means.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

V2 = "application/vnd.kafka.v2+json"
V2_JSON_EMBEDDED = "application/vnd.kafka.json.v2+json"


class RestProxyError(Exception):
    """A REST Proxy v2 error envelope: {"error_code": ..., "message": ...}."""

    def __init__(self, http_status, error_code, message):
        super().__init__(f"HTTP {http_status} / {error_code}: {message}")
        self.http_status = http_status
        self.error_code = error_code
        self.message = message


class Response:
    """A non-error transport result: status, headers, parsed JSON body."""

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def origin_of(url):
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _parse(stream):
    raw = stream.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


class Transport:
    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(
            f"{username}:{password}".encode("utf-8")).decode("ascii")
        self._auth = "Basic " + token
        self._opener = urllib.request.build_opener(_NoRedirect())

    @property
    def origin(self):
        return origin_of(self.base_url)

    def request(self, method, url, body=None, content_type=V2, accept=V2):
        """Issue one request. Relative URLs resolve against the base URL.

        2xx and 3xx come back as a Response (redirects are never followed);
        anything else raises RestProxyError built from the v2 error envelope.
        """
        if url.startswith("/"):
            url = self.base_url + url
        data = None
        headers = {"Accept": accept, "Authorization": self._auth}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with self._opener.open(req, timeout=10) as resp:
                return Response(resp.status, dict(resp.headers), _parse(resp))
        except urllib.error.HTTPError as err:
            if 300 <= err.code < 400:
                return Response(err.code, dict(err.headers), None)
            envelope = _parse(err)
            code = None
            message = ""
            if isinstance(envelope, dict):
                code = envelope.get("error_code")
                message = envelope.get("message") or ""
            raise RestProxyError(err.code, code, message) from None
