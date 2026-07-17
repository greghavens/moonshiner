"""Minimal Terraform Enterprise /api/v2 transport (stdlib only).

This client is deliberately small and already in production use: it sends
the bearer token and JSON:API media type, and it never raises on non-2xx
responses — callers get the status and parsed body back so they can decode
JSON:API error documents themselves.
"""

import json
import urllib.error
import urllib.request

API_MEDIA_TYPE = "application/vnd.api+json"


class TFEClient:
    """One Terraform Enterprise host + token."""

    def __init__(self, base_url, token, opener=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._opener = opener or urllib.request.build_opener()

    def request(self, method, path, body=None):
        """Send one /api/v2 request.

        Returns (status, parsed_json_or_None). Non-2xx responses are not
        raised; their status and parsed error document are returned.
        """
        headers = {"Authorization": "Bearer " + self.token}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = API_MEDIA_TYPE
        req = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with self._opener.open(req) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as err:
            status = err.code
            raw = err.read()
        if not raw:
            return status, None
        return status, json.loads(raw.decode("utf-8"))
