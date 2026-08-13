"""Deterministic in-process Snapservice appliance fixture.

The fixture accepts the real ``urllib.request.Request`` objects emitted by the
client and returns HTTP-like responses without opening a network socket.  It is
pinned to docs/contract.json, serves only the four recorded operations, and
records every request's exact wire shape in a JSON Lines log.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

SESSION_HEADER = "vmware-api-session-id"
BASE_PATH = "/api/snapservice"

RETENTION_UNITS = {"MINUTE", "HOUR", "DAY", "WEEK", "MONTH", "YEAR"}
CREATE_SPEC_PROPERTIES = {"name", "retention"}
LIST_QUERY_PARAMETERS = {"pgs", "names", "states", "vms", "cluster_pairs"}

_ORIGINAL_URLOPEN = urllib.request.urlopen
_ACTIVE_APPLIANCE = None


def _dispatch_urlopen(request, timeout=None):
    if _ACTIVE_APPLIANCE is None:
        raise RuntimeError("no MockAppliance context is active")
    return _ACTIVE_APPLIANCE._urlopen(request, timeout=timeout)


def install_transport():
    """Install the adapter before importing the client under test."""
    urllib.request.urlopen = _dispatch_urlopen


def restore_transport():
    urllib.request.urlopen = _ORIGINAL_URLOPEN

# Each non-terminal status appears in the successful script. The final status
# repeats once reached, as it would on an appliance queried again later.
TASK_SCRIPTS = {
    "pg-nightly": ["PENDING", "RUNNING", "BLOCKED", "SUCCEEDED"],
    "pg-broken": ["PENDING", "RUNNING", "FAILED"],
    "pg-hung": ["RUNNING"],
}

PROTECTION_GROUPS = {
    "pg-nightly": {"name": "nightly-tier1", "status": "ACTIVE"},
    "pg-broken": {"name": "quarterly-archive", "status": "ACTIVE"},
    "pg-hung": {"name": "dr-replica", "status": "ACTIVE"},
}


def _localizable(identifier, message):
    return {"id": identifier, "default_message": message, "args": []}


class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.sequence = 0
        self.tasks = {}
        self.snapshots = {}
        self.task_counter = 0
        self.snapshot_counter = 0

    def next_sequence(self):
        self.sequence += 1
        return self.sequence

    def new_task(self, cluster, pg, spec):
        self.task_counter += 1
        task_id = "task-%d" % self.task_counter
        self.tasks[task_id] = {
            "cluster": cluster,
            "pg": pg,
            "spec": spec,
            "script": list(TASK_SCRIPTS.get(pg, TASK_SCRIPTS["pg-nightly"])),
            "polls": 0,
            "snapshot": None,
        }
        return task_id

    def new_snapshot(self, cluster, pg, spec):
        self.snapshot_counter += 1
        snapshot_id = "snap-%d" % self.snapshot_counter
        record = {
            "name": spec["name"],
            "snapshot_type": "ONE_TIME",
            "start_time": "2026-05-13T11:24:05.000Z",
            "end_time": "2026-05-13T11:24:41.000Z",
            "pg": pg,
            "vm_snapshots": [
                {
                    "snapshot": snapshot_id + "-vm-1",
                    "name": spec["name"],
                    "created_at": "2026-05-13T11:24:38.000Z",
                    "vm": "vm-4021",
                }
            ],
        }
        if "retention" in spec:
            record["expires_at"] = "2026-05-20T11:24:41.000Z"
        self.snapshots[(cluster, pg, snapshot_id)] = record
        return snapshot_id


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8") if payload is not None else b""

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class MockAppliance:
    """Context manager implementing a contract-faithful urllib transport."""

    def __init__(self, log_path):
        self.log_path = Path(log_path)
        self.state = _State()

    def __enter__(self):
        global _ACTIVE_APPLIANCE
        if _ACTIVE_APPLIANCE is not None:
            raise RuntimeError("MockAppliance contexts may not be nested")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
        _ACTIVE_APPLIANCE = self
        return self

    def __exit__(self, exc_type, exc, tb):
        global _ACTIVE_APPLIANCE
        _ACTIVE_APPLIANCE = None
        return False

    @property
    def base_url(self):
        return "http://snapservice.test/api"

    def requests(self):
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        entries = [json.loads(line) for line in lines if line.strip()]
        return sorted(entries, key=lambda item: item["sequence"])

    # -- urllib adapter ---------------------------------------------------

    def _urlopen(self, request, timeout=None):
        del timeout
        entry = self._record(request)
        return self._route(request.full_url, entry)

    def _record(self, request):
        split = urlsplit(request.full_url)
        raw_body = request.data or b""
        try:
            parsed_body = json.loads(raw_body.decode("utf-8")) if raw_body else None
        except (ValueError, UnicodeDecodeError):
            parsed_body = None
        all_headers = {name.lower(): value for name, value in request.header_items()}
        entry = {
            "method": request.get_method(),
            "path": split.path,
            "raw_query": split.query,
            "query_pairs": parse_qsl(split.query, keep_blank_values=True),
            "headers": {
                "content-type": all_headers.get("content-type"),
                "accept": all_headers.get("accept"),
                SESSION_HEADER: all_headers.get(SESSION_HEADER),
                "authorization": all_headers.get("authorization"),
            },
            "raw_body": raw_body.decode("utf-8", "replace") if raw_body else "",
            "body": parsed_body,
        }
        with self.state.lock:
            entry["sequence"] = self.state.next_sequence()
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    @staticmethod
    def _ok(status, payload):
        return _Response(status, payload)

    @staticmethod
    def _error(url, status, error_type, message):
        payload = {
            "error_type": error_type,
            "messages": [_localizable("com.vmware.snapservice.mock", message)],
        }
        body = json.dumps(payload).encode("utf-8")
        raise urllib.error.HTTPError(
            url, status, message, {"Content-Type": "application/json"}, io.BytesIO(body)
        )

    # -- routing ----------------------------------------------------------

    def _route(self, url, entry):
        method = entry["method"]
        path = entry["path"]
        if not path.startswith(BASE_PATH):
            return self._error(url, 404, "Vapi.Std.Errors.NotFound", "no such resource: %s" % path)
        if not entry["headers"].get(SESSION_HEADER):
            return self._error(
                url,
                401,
                "Vapi.Std.Errors.Unauthenticated",
                "missing %s header" % SESSION_HEADER,
            )

        segments = [segment for segment in path[len(BASE_PATH) :].split("/") if segment]
        if method == "GET" and len(segments) == 2 and segments[0] == "tasks":
            return self._tasks_get(url, segments[1])
        if segments[:1] == ["clusters"]:
            if method == "GET" and len(segments) == 3 and segments[2] == "protection-groups":
                return self._protection_groups_list(url, entry)
            if len(segments) >= 4 and segments[2] == "protection-groups":
                cluster, pg = segments[1], segments[3]
                if method == "POST" and len(segments) == 5 and segments[4] == "snapshots":
                    return self._snapshots_create(url, cluster, pg, entry)
                if method == "GET" and len(segments) == 6 and segments[4] == "snapshots":
                    return self._snapshots_get(url, cluster, pg, segments[5])
        return self._error(
            url,
            404,
            "Vapi.Std.Errors.NotFound",
            "operation not served by this contract: %s %s" % (method, path),
        )

    # -- operations -------------------------------------------------------

    def _protection_groups_list(self, url, entry):
        seen = set()
        for name, value in entry["query_pairs"]:
            if name not in LIST_QUERY_PARAMETERS:
                return self._error(
                    url, 400, "Vapi.Std.Errors.InvalidArgument", "unknown query parameter: %s" % name
                )
            if value == "":
                return self._error(
                    url,
                    400,
                    "Vapi.Std.Errors.InvalidArgument",
                    "query parameter %s was sent empty; an unset optional parameter must be omitted"
                    % name,
                )
            if (name, value) in seen:
                return self._error(
                    url,
                    400,
                    "Vapi.Std.Errors.InvalidArgument",
                    "query parameter %s repeats the value %s" % (name, value),
                )
            seen.add((name, value))

        wanted_names = {value for name, value in entry["query_pairs"] if name == "names"}
        wanted_pgs = {value for name, value in entry["query_pairs"] if name == "pgs"}
        items = []
        for pg_id, info in sorted(PROTECTION_GROUPS.items()):
            if wanted_pgs and pg_id not in wanted_pgs:
                continue
            if wanted_names and info["name"] not in wanted_names:
                continue
            items.append(
                {
                    "pg": pg_id,
                    "info": {
                        "name": info["name"],
                        "status": info["status"],
                        "target_entities": {"vms": ["vm-4021"]},
                        "snapshot_policies": [],
                        "vms": ["vm-4021"],
                        "snapshots": [],
                        "locked": False,
                    },
                }
            )
        return self._ok(200, {"items": items})

    def _snapshots_create(self, url, cluster, pg, entry):
        query = dict(entry["query_pairs"])
        if query.get("vmw-task") != "true":
            return self._error(
                url,
                400,
                "Vapi.Std.Errors.InvalidArgument",
                "asynchronous operation requires vmw-task=true",
            )
        if set(query) != {"vmw-task"}:
            return self._error(
                url,
                400,
                "Vapi.Std.Errors.InvalidArgument",
                "unexpected query parameters: %s" % sorted(set(query) - {"vmw-task"}),
            )
        if (entry["headers"].get("content-type") or "").split(";")[0].strip() != "application/json":
            return self._error(
                url, 400, "Vapi.Std.Errors.InvalidArgument", "Content-Type must be application/json"
            )

        spec = entry["body"]
        if not isinstance(spec, dict):
            return self._error(
                url, 400, "Vapi.Std.Errors.InvalidArgument", "request body must be a JSON object"
            )
        unknown = set(spec) - CREATE_SPEC_PROPERTIES
        if unknown:
            return self._error(
                url,
                400,
                "Vapi.Std.Errors.InvalidArgument",
                "CreateSpec has no such properties: %s" % sorted(unknown),
            )
        if not isinstance(spec.get("name"), str) or not spec["name"]:
            return self._error(
                url, 400, "Vapi.Std.Errors.InvalidArgument", "CreateSpec.name is required"
            )
        if "retention" in spec:
            retention = spec["retention"]
            if not isinstance(retention, dict) or set(retention) != {"unit", "duration"}:
                return self._error(
                    url,
                    400,
                    "Vapi.Std.Errors.InvalidArgument",
                    "CreateSpec.retention must contain unit and duration",
                )
            if retention["unit"] not in RETENTION_UNITS:
                return self._error(
                    url, 400, "Vapi.Std.Errors.InvalidArgument", "invalid retention unit"
                )
            if not isinstance(retention["duration"], int) or isinstance(retention["duration"], bool):
                return self._error(
                    url, 400, "Vapi.Std.Errors.InvalidArgument", "retention duration must be an integer"
                )
        if pg not in PROTECTION_GROUPS:
            return self._error(
                url, 404, "Vapi.Std.Errors.NotFound", "no such protection group: %s" % pg
            )
        with self.state.lock:
            task_id = self.state.new_task(cluster, pg, spec)
        return self._ok(202, task_id)

    def _tasks_get(self, url, task_id):
        with self.state.lock:
            task = self.state.tasks.get(task_id)
            if task is None:
                return self._error(
                    url, 404, "Vapi.Std.Errors.NotFound", "no such task: %s" % task_id
                )
            index = min(task["polls"], len(task["script"]) - 1)
            status = task["script"][index]
            task["polls"] += 1
            if status == "SUCCEEDED" and task["snapshot"] is None:
                task["snapshot"] = self.state.new_snapshot(
                    task["cluster"], task["pg"], task["spec"]
                )
            snapshot_id = task["snapshot"]

        info = {
            "cancelable": False,
            "description": _localizable(
                "com.vmware.snapservice.protection_group.snapshot.create",
                "Create a protection group snapshot",
            ),
            "operation": "create",
            "service": "com.vmware.snapservice.clusters.protection_groups.snapshots",
            "status": status,
        }
        if status in ("RUNNING", "BLOCKED", "SUCCEEDED", "FAILED"):
            info["start_time"] = "2026-05-13T11:24:05.000Z"
            info["progress"] = {
                "total": 100,
                "completed": 100 if status in ("SUCCEEDED", "FAILED") else 40,
                "message": _localizable(
                    "com.vmware.snapservice.task.progress", "Quiescing virtual machines"
                ),
            }
        if status == "SUCCEEDED":
            info["end_time"] = "2026-05-13T11:24:41.000Z"
            info["result"] = snapshot_id
        elif status == "FAILED":
            info["end_time"] = "2026-05-13T11:24:33.000Z"
            info["error"] = {
                "error_type": "Vapi.Std.Errors.Error",
                "messages": [
                    _localizable(
                        "com.vmware.snapservice.snapshot.quiesce_failed",
                        "Snapshot quiesce failed on virtual machine vm-4021",
                    )
                ],
            }
        return self._ok(200, info)

    def _snapshots_get(self, url, cluster, pg, snapshot_id):
        with self.state.lock:
            record = self.state.snapshots.get((cluster, pg, snapshot_id))
        if record is None:
            return self._error(
                url, 404, "Vapi.Std.Errors.NotFound", "no such snapshot: %s" % snapshot_id
            )
        return self._ok(200, record)
