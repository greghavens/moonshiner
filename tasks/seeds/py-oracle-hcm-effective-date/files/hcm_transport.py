"""Thin HTTP session for our Oracle Fusion Cloud HCM pod.

Every request carries the integration user's basic auth, the pinned
REST-Framework-Version, and Accept: application/json. Responses are wrapped
with lowercased header names so callers can read ETag et al. without
worrying about case. Non-2xx responses are returned, not raised — the
caller decides what a failure means for its operation.
"""

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

API_ROOT = "/hcmRestApi/resources/11.13.18.05"
FRAMEWORK_VERSION = "4"


@dataclass
class HcmResponse:
    status: int
    headers: dict
    body: object


class HcmSession:
    def __init__(self, base_url, user, password):
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        self._auth = f"Basic {token}"

    def request(self, method, path, headers=None, body=None):
        req_headers = {
            "Authorization": self._auth,
            "REST-Framework-Version": FRAMEWORK_VERSION,
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        for name, value in req_headers.items():
            req.add_header(name, value)
        try:
            with urllib.request.urlopen(req) as resp:
                return self._wrap(resp.status, resp.headers, resp.read())
        except urllib.error.HTTPError as err:
            return self._wrap(err.code, err.headers, err.read())

    @staticmethod
    def _wrap(status, headers, raw):
        lowered = {name.lower(): value for name, value in headers.items()}
        parsed = json.loads(raw.decode("utf-8")) if raw else None
        return HcmResponse(status=status, headers=lowered, body=parsed)
