"""Minimal HTTP transport for the VCF Operations for Networks appliance API.

This module knows nothing about individual operations. It sends exactly what it
is handed and returns exactly what came back, including 4xx and 5xx responses --
it never raises for a non-2xx status. Building the correct request payload for
each operation is the caller's job.

Standard library only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class HttpResponse:
    """A response from the appliance."""

    __slots__ = ("status", "headers", "body")

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        """Decode the body as JSON, or return None when the body is empty."""
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def __repr__(self):  # pragma: no cover - debugging aid
        return "HttpResponse(status=%r, body=%r)" % (self.status, self.body)


def send(method, url, headers=None, json_body=None, timeout=10.0):
    """Send one request.

    ``json_body`` is serialized with ``json.dumps`` exactly as given: keys that
    are absent from the mapping are absent from the wire, and keys whose value is
    ``None`` are serialized as JSON ``null``. When ``json_body`` is ``None`` no
    body and no Content-Type header are sent.
    """
    data = None
    request_headers = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method)
    for name, value in request_headers.items():
        request.add_header(name, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as error:  # non-2xx is a normal outcome here
        with error:
            return HttpResponse(error.code, dict(error.headers), error.read())
