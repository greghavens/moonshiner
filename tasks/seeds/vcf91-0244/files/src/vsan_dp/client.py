"""Snapshot appliance client.

Implements the four operations named in docs/contract.json:

  Snapservice.Clusters.ProtectionGroups_list
  Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task
  Snapservice.Tasks_get
  Snapservice.Clusters.ProtectionGroups.Snapshots_get

Standard library only.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

SESSION_HEADER = "vmware-api-session-id"

#: Task statuses that mean the appliance is still working.
NON_TERMINAL_STATUSES = ("PENDING", "RUNNING", "BLOCKED")
#: Task statuses that mean the appliance has stopped working on the task.
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED")

DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_POLL_TIMEOUT = 1800.0


class VsanDpError(Exception):
    """Base class for every error this package raises."""


class ApiError(VsanDpError):
    """The appliance answered with a vAPI error response."""

    def __init__(self, status, error_type, message):
        super().__init__("%s (HTTP %s): %s" % (error_type, status, message))
        self.status = status
        self.error_type = error_type
        self.message = message


class TaskFailedError(VsanDpError):
    """A polled task reached the FAILED terminal status."""

    def __init__(self, task_id, info, message):
        super().__init__("task %s failed: %s" % (task_id, message))
        self.task_id = task_id
        self.info = info
        self.message = message


class TaskTimeoutError(VsanDpError):
    """A polled task did not reach a terminal status within the timeout."""

    def __init__(self, task_id, info, timeout):
        super().__init__(
            "task %s did not reach a terminal status within %.3fs" % (task_id, timeout)
        )
        self.task_id = task_id
        self.info = info
        self.timeout = timeout


@dataclass(frozen=True)
class RetentionPeriod:
    """Snapservice.RetentionPeriod: how long a snapshot must be retained."""

    unit: str
    duration: int

    def to_wire(self):
        return {"unit": self.unit, "duration": self.duration}


def _first_message(messages):
    if isinstance(messages, list) and messages:
        head = messages[0]
        if isinstance(head, dict):
            return head.get("default_message") or head.get("id") or ""
    return ""


class SnapshotClient:
    """Client for protection group snapshot operations."""

    def __init__(
        self,
        base_url,
        session_id,
        poll_interval=DEFAULT_POLL_INTERVAL,
        poll_timeout=DEFAULT_POLL_TIMEOUT,
        timeout=30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.timeout = timeout

    # -- transport ---------------------------------------------------------

    def _url(self, path, query=None):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        return url

    def _request(self, method, path, query=None, body=None):
        data = None
        headers = {SESSION_HEADER: self.session_id, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            self._url(path, query), data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                payload = json.loads(raw.decode("utf-8")) if raw else None
                return response.status, payload
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                payload = {}
            raise ApiError(
                exc.code,
                payload.get("error_type", "Vapi.Std.Errors.Error"),
                _first_message(payload.get("messages")),
            ) from None

    @staticmethod
    def _quote(value):
        return urllib.parse.quote(str(value), safe="")

    # -- Snapservice.Clusters.ProtectionGroups_list -------------------------

    def list_protection_groups(
        self,
        cluster,
        pgs=None,
        names=None,
        states=None,
        vms=None,
        cluster_pairs=None,
    ):
        """Return the Snapservice.Clusters.ProtectionGroups.ListResult items."""
        query = [
            ("pgs", pgs or []),
            ("names", names or []),
            ("states", states or []),
            ("vms", vms or []),
            ("cluster_pairs", cluster_pairs or []),
        ]
        path = "/snapservice/clusters/%s/protection-groups" % self._quote(cluster)
        _status, payload = self._request("GET", path, query=query)
        return (payload or {}).get("items", [])

    # -- Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task --------

    def create_snapshot(self, cluster, pg, name, retention=None):
        """Start a one-time protection group snapshot; return the task identifier."""
        spec = {
            "name": name,
            "retention": retention.to_wire() if retention is not None else None,
        }
        path = "/snapservice/clusters/%s/protection-groups/%s/snapshots" % (
            self._quote(cluster),
            self._quote(pg),
        )
        _status, payload = self._request(
            "POST", path, query=[("vmw-task", "true")], body=spec
        )
        return payload

    # -- Snapservice.Tasks_get ---------------------------------------------

    def get_task(self, task_id):
        """Return the Snapservice.Tasks.Info for a single task."""
        path = "/snapservice/tasks/%s" % self._quote(task_id)
        _status, payload = self._request("GET", path)
        return payload

    def wait_for_task(self, task_id, interval=None, timeout=None):
        """Return the task info once the appliance has finished the operation."""
        return self.get_task(task_id)

    # -- Snapservice.Clusters.ProtectionGroups.Snapshots_get ----------------

    def get_snapshot(self, cluster, pg, snapshot):
        """Return the Snapservice.Clusters.ProtectionGroups.Snapshots.Info."""
        path = "/snapservice/clusters/%s/protection-groups/%s/snapshots/%s" % (
            self._quote(cluster),
            self._quote(pg),
            self._quote(snapshot),
        )
        _status, payload = self._request("GET", path)
        return payload

    # -- orchestration -----------------------------------------------------

    def take_snapshot(
        self, cluster, pg, name, retention=None, interval=None, timeout=None
    ):
        """Take a one-time snapshot and return its Snapshots.Info once it exists."""
        task_id = self.create_snapshot(cluster, pg, name, retention=retention)
        info = self.wait_for_task(task_id, interval=interval, timeout=timeout)
        return self.get_snapshot(cluster, pg, info.get("result"))
