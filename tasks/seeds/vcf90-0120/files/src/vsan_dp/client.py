"""Client for the two Snapservice operations in docs/contract.json."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


SESSION_HEADER = "vmware-api-session-id"
NON_TERMINAL_STATUSES = ("PENDING", "RUNNING", "BLOCKED")


class VsanDpError(Exception):
    """Base class for package errors."""


class ApiError(VsanDpError):
    """The appliance returned an HTTP error response."""

    def __init__(self, status, error_type, message):
        super().__init__("%s (HTTP %s): %s" % (error_type, status, message))
        self.status = status
        self.error_type = error_type
        self.message = message


class TaskFailedError(VsanDpError):
    """An asynchronous task reached FAILED."""

    def __init__(self, task_id, info):
        super().__init__("task %s failed" % task_id)
        self.task_id = task_id
        self.info = info


@dataclass(frozen=True)
class RetentionPeriod:
    """Snapservice.RetentionPeriod."""

    unit: str
    duration: int

    def to_wire(self):
        return {"unit": self.unit, "duration": self.duration}


def _first_message(payload):
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        return messages[0].get("default_message") or messages[0].get("id") or ""
    return ""


class SnapshotClient:
    """Client for creating a protection-group snapshot and polling its task."""

    def __init__(
        self,
        base_url,
        access_token,
        refresh_access_token,
        poll_interval=1.0,
        timeout=30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.refresh_access_token = refresh_access_token
        self.poll_interval = poll_interval
        self.timeout = timeout

    @staticmethod
    def _quote(value):
        return urllib.parse.quote(str(value), safe="")

    def _url(self, path, query=None):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def _request(self, method, path, query=None, body=None):
        data = None
        headers = {
            SESSION_HEADER: self.access_token,
            "Accept": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self._url(path, query), data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                payload = {}
            raise ApiError(
                exc.code,
                payload.get("error_type", "Vapi.Std.Errors.Error"),
                _first_message(payload),
            ) from None

    def create_snapshot(self, cluster, pg, name, retention=None):
        """Start a one-time snapshot and return its task identifier."""
        body = {"name": name}
        if retention is not None:
            body["retention"] = retention.to_wire()
        path = "/snapservice/clusters/%s/protection-groups/%s/snapshots" % (
            self._quote(cluster),
            self._quote(pg),
        )
        return self._request(
            "POST", path, query=[("vmw-task", "true")], body=body
        )

    def get_task(self, task_id):
        """Return Snapservice.Tasks.Info for task_id."""
        return self._request("GET", "/snapservice/tasks/%s" % self._quote(task_id))

    def wait_for_task(self, task_id):
        """Poll until task_id reaches SUCCEEDED or FAILED."""
        while True:
            info = self.get_task(task_id) or {}
            status = info.get("status")
            if status == "SUCCEEDED":
                return info
            if status == "FAILED":
                raise TaskFailedError(task_id, info)
            if status not in NON_TERMINAL_STATUSES:
                raise VsanDpError("task %s returned unknown status %r" % (task_id, status))
            time.sleep(self.poll_interval)

    def take_snapshot(self, cluster, pg, name, retention=None):
        """Create a snapshot, wait for its task, and return the result identifier."""
        task_id = self.create_snapshot(cluster, pg, name, retention=retention)
        return self.wait_for_task(task_id).get("result")
