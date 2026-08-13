"""A thin HTTP transport over urllib.

This layer is deliberately dumb: it sends exactly the method, URL, headers and body
bytes it is handed, and it never invents a header or a query string of its own. Deciding
what belongs on the wire is the caller's job.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import NamedTuple, Optional


class Response(NamedTuple):
    status: int
    headers: dict
    body: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self):
        """Decode the body as JSON, or return None when the body is empty."""
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


def request(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    body: Optional[bytes] = None,
    timeout: float = 30.0,
) -> Response:
    """Send one HTTP request and return the response without raising on 4xx/5xx.

    ``body`` is sent verbatim. Pass None to send no body at all: urllib will then omit
    both the payload and any Content-Length header.
    """
    req = urllib.request.Request(url, data=body, method=method.upper())
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return Response(
                status=response.status,
                headers={k.lower(): v for k, v in response.headers.items()},
                body=response.read(),
            )
    except urllib.error.HTTPError as exc:
        return Response(
            status=exc.code,
            headers={k.lower(): v for k, v in (exc.headers or {}).items()},
            body=exc.read(),
        )
