"""In-process HTTP mock of the VCF Automation provisioning service.

PROTECTED FILE -- do not modify.

The mock is pinned to ``docs/contract.json``: it serves the three operations the
contract names and answers everything else with the service's error document. It
is not a general purpose fake; it exists so the acceptance tests can inspect the
exact bytes a client puts on the wire.

Every request is appended to a JSON Lines log. One object per line:

    {
      "seq": 0,
      "method": "POST",
      "path": "/iaas/api/machines",
      "query": {"apiVersion": ["2021-07-15"]},
      "headers": {"authorization": "...", "content-type": "application/json"},
      "raw_body": "{...}",
      "body": {...} | null,
      "body_error": "..."        # only when raw_body was not valid JSON
    }
"""

import io
import json
import socket
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

__all__ = ["MockConfig", "MockProvisioningService", "read_log"]

# Wire property names documented for MachineSpecification.
MACHINE_SPECIFICATION_PROPERTIES = frozenset({
    "name", "projectId", "flavor", "flavorRef", "image", "imageRef",
    "deploymentId", "description", "machineCount", "customProperties",
    "nics", "disks", "tags", "bootConfig", "bootConfigSettings",
    "constraints", "imageDiskConstraints", "remoteAccess", "saltConfiguration",
})
MACHINE_SPECIFICATION_REQUIRED = (
    "name", "projectId", "flavor", "flavorRef", "image", "imageRef",
)

NIC_PROPERTIES = frozenset({
    "name", "description", "deviceIndex", "networkId", "fabricNetworkId",
    "addresses", "macAddress", "securityGroupIds", "customProperties",
})
DISK_PROPERTIES = frozenset({
    "name", "description", "blockDeviceId", "scsiController", "unitNumber",
    "diskAttachmentProperties",
})
TAG_PROPERTIES = frozenset({"key", "value"})
BOOT_CONFIG_PROPERTIES = frozenset({"content"})
CONSTRAINT_PROPERTIES = frozenset({"mandatory", "expression"})

_NESTED_OBJECT_PROPERTIES = {
    "nics": NIC_PROPERTIES,
    "disks": DISK_PROPERTIES,
    "tags": TAG_PROPERTIES,
    "constraints": CONSTRAINT_PROPERTIES,
}


@dataclass
class MockConfig:
    """Scenario knobs. Everything is deterministic; nothing is time dependent."""

    token: str = "vcfa-mock-token"
    api_version: str = "2021-07-15"
    #: number of tracker reads answered with a non-terminal state before the
    #: terminal state is reported
    inprogress_polls: int = 2
    #: terminal state eventually reported: "FINISHED" or "FAILED"
    terminal_status: str = "FINISHED"
    failure_message: str = "Provisioning failed: no datastore matched the placement constraints"
    #: when true the tracker never becomes terminal
    never_finish: bool = False
    request_id: str = "9c8f3b2a-0f24-4a3e-9d1b-6f2c4a7e5d10"
    machine_id: str = "3f1d5e7b-2c48-4d9a-8b60-1e5a9c3f7d24"
    deployment_id: str = "b7a41c92-6d38-4f51-9c07-2a8e6b4d3f15"


@dataclass
class _State:
    polls: int = 0
    seq: int = 0
    created: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class MockProvisioningService:
    """Intercepts stdlib HTTP connections without opening a network socket."""

    def __init__(self, log_path: str, config: Optional[MockConfig] = None):
        self.config = config or MockConfig()
        self.log_path = log_path
        self._state = _State()
        self._original_create_connection = None
        open(self.log_path, "w").close()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> str:
        if self._original_create_connection is None:
            self._original_create_connection = socket.create_connection
            socket.create_connection = self._connect
        return self.base_url

    def stop(self) -> None:
        if self._original_create_connection is not None:
            socket.create_connection = self._original_create_connection
            self._original_create_connection = None

    def __enter__(self) -> "MockProvisioningService":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:44119"

    def requests(self) -> List[Dict[str, Any]]:
        return read_log(self.log_path)

    def _connect(self, *args, **kwargs):
        return _MemorySocket(self)

    # -- raw HTTP plumbing -------------------------------------------------
    def _dispatch(self, request_bytes: bytes) -> bytes:
        head, separator, body_bytes = request_bytes.partition(b"\r\n\r\n")
        if not separator:
            return self._response(400, {"message": "Malformed HTTP request."})

        lines = head.decode("iso-8859-1").split("\r\n")
        method, target, _http_version = lines[0].split(" ", 2)
        headers = {}
        for line in lines[1:]:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()

        length = int(headers.get("content-length") or 0)
        body_bytes = body_bytes[:length] if length else b""
        parsed = urlparse(target)
        raw = body_bytes.decode("utf-8") if body_bytes else ""
        entry = {
            "method": method,
            "path": parsed.path,
            "query": parse_qs(parsed.query, keep_blank_values=True),
            "headers": {
                key: value for key, value in headers.items()
                if key in ("authorization", "content-type", "accept")
            },
            "raw_body": raw,
            "body": None,
        }
        if raw:
            try:
                entry["body"] = json.loads(raw)
            except ValueError as exc:
                entry["body_error"] = str(exc)

        with self._state.lock:
            entry["seq"] = self._state.seq
            self._state.seq += 1
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")

        status, payload = self._route(entry)
        return self._response(status, payload)

    @staticmethod
    def _response(status: int, payload: Dict[str, Any]) -> bytes:
        body = json.dumps(payload).encode("utf-8")
        reason = HTTPStatus(status).phrase
        headers = (
            "HTTP/1.1 {0} {1}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {2}\r\n"
            "Connection: close\r\n\r\n"
        ).format(status, reason, len(body)).encode("ascii")
        return headers + body

    @staticmethod
    def _error(status: int, message: str):
        return status, {"message": message, "statusCode": status}

    # -- routing -----------------------------------------------------------
    def _route(self, entry: Dict[str, Any]):
        path = entry["path"].rstrip("/") or "/"

        if entry["headers"].get("authorization") != self.config.token:
            return self._error(403, "Not authorized to perform this operation.")

        api_version = entry["query"].get("apiVersion")
        if not api_version or api_version[0] != self.config.api_version:
            return self._error(
                400,
                "This endpoint serves apiVersion {0} only.".format(
                    self.config.api_version),
            )

        segments = [segment for segment in path.split("/") if segment]
        if entry["method"] == "POST" and segments == ["iaas", "api", "machines"]:
            return self._create_machine(entry)
        if (entry["method"] == "GET" and len(segments) == 4
                and segments[:3] == ["iaas", "api", "request-tracker"]):
            return self._get_request_tracker(segments[3])
        if (entry["method"] == "GET" and len(segments) == 4
                and segments[:3] == ["iaas", "api", "machines"]):
            return self._get_machine(segments[3])
        return self._error(
            404,
            "No operation is served at {0} {1}.".format(entry["method"], path),
        )

    # -- operations --------------------------------------------------------
    def _create_machine(self, entry: Dict[str, Any]):
        if "body_error" in entry:
            return self._error(400, "Request body is not valid JSON.")
        body = entry["body"]
        if not isinstance(body, dict):
            return self._error(400, "Request body must be a MachineSpecification object.")

        problem = _validate_machine_specification(body)
        if problem is not None:
            return self._error(400, problem)

        with self._state.lock:
            self._state.created = True
            self._state.polls = 0

        return 202, {
            "id": self.config.request_id,
            "name": "Provisioning",
            "progress": 0,
            "status": "INPROGRESS",
            "message": "Request accepted.",
            "selfLink": "/iaas/api/request-tracker/{0}".format(self.config.request_id),
            "deploymentId": self.config.deployment_id,
        }

    def _get_request_tracker(self, request_id: str):
        if request_id != self.config.request_id or not self._state.created:
            return self._error(404, "Request {0} was not found.".format(request_id))

        with self._state.lock:
            self._state.polls += 1
            poll = self._state.polls

        common = {
            "id": self.config.request_id,
            "name": "Provisioning",
            "selfLink": "/iaas/api/request-tracker/{0}".format(self.config.request_id),
            "deploymentId": self.config.deployment_id,
        }
        if self.config.never_finish or poll <= self.config.inprogress_polls:
            return 200, dict(common, progress=min(90, 10 * poll), status="INPROGRESS",
                             message="Provisioning in progress.")
        if self.config.terminal_status == "FAILED":
            return 200, dict(common, progress=100, status="FAILED",
                             message=self.config.failure_message)
        return 200, dict(
            common,
            progress=100,
            status="FINISHED",
            message="Provisioning completed.",
            resources=["/iaas/api/machines/{0}".format(self.config.machine_id)],
        )

    def _get_machine(self, machine_id: str):
        if machine_id != self.config.machine_id or not self._state.created:
            return self._error(404, "Machine {0} was not found.".format(machine_id))
        if (self.config.terminal_status != "FINISHED"
                or self._state.polls <= self.config.inprogress_polls):
            return self._error(404, "Machine {0} was not found.".format(machine_id))
        return 200, {
            "id": self.config.machine_id,
            "name": "app-node-01",
            "powerState": "ON",
            "address": "10.24.6.51",
            "externalId": "vm-4417",
            "externalRegionId": "Datacenter:datacenter-21",
            "externalZoneId": "domain-c31",
            "projectId": "0f3a1c58-9b74-42d6-8e05-7c1d2b9a6e43",
            "deploymentId": self.config.deployment_id,
            "provisioningStatus": "READY",
            "createdAt": "2025-11-04T09:12:44.318Z",
            "updatedAt": "2025-11-04T09:18:02.907Z",
            "_links": {
                "self": {"href": "/iaas/api/machines/{0}".format(self.config.machine_id)},
            },
        }


def read_log(log_path: str) -> List[Dict[str, Any]]:
    """Return the recorded requests, in the order they were received."""
    entries = []
    with open(log_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    entries.sort(key=lambda entry: entry["seq"])
    return entries


class _MemorySocket:
    """Enough of the socket API for :mod:`http.client` to exchange one request."""

    def __init__(self, service: MockProvisioningService):
        self.service = service
        self.request = bytearray()

    def sendall(self, data) -> None:
        self.request.extend(data)

    def setsockopt(self, *args) -> None:
        pass

    def settimeout(self, timeout) -> None:
        pass

    def makefile(self, mode, buffering=None):
        return io.BytesIO(self.service._dispatch(bytes(self.request)))

    def close(self) -> None:
        pass


def _validate_machine_specification(body: Dict[str, Any]) -> Optional[str]:
    """Return an error message when the body is not a valid MachineSpecification."""
    unknown = sorted(set(body) - MACHINE_SPECIFICATION_PROPERTIES)
    if unknown:
        return "Unrecognized MachineSpecification properties: {0}.".format(", ".join(unknown))

    missing = [name for name in MACHINE_SPECIFICATION_REQUIRED if name not in body]
    if missing:
        return "Missing required properties: {0}.".format(", ".join(missing))

    null_keys = sorted(key for key, value in body.items() if value is None)
    if null_keys:
        return "Properties must be omitted rather than null: {0}.".format(", ".join(null_keys))

    for key in ("name", "projectId", "flavor", "flavorRef", "image", "imageRef"):
        if not isinstance(body[key], str) or not body[key]:
            return "Property {0} must be a non-empty string.".format(key)

    if "machineCount" in body and not isinstance(body["machineCount"], int):
        return "Property machineCount must be an integer."

    for key in ("customProperties",):
        if key in body and not isinstance(body[key], dict):
            return "Property {0} must be an object.".format(key)

    if "bootConfig" in body:
        boot_config = body["bootConfig"]
        if not isinstance(boot_config, dict):
            return "Property bootConfig must be an object."
        unknown = sorted(set(boot_config) - BOOT_CONFIG_PROPERTIES)
        if unknown:
            return "Unrecognized bootConfig properties: {0}.".format(", ".join(unknown))
        if any(value is None for value in boot_config.values()):
            return "Properties must be omitted rather than null: bootConfig."

    for collection, allowed in _NESTED_OBJECT_PROPERTIES.items():
        if collection not in body:
            continue
        items = body[collection]
        if not isinstance(items, list):
            return "Property {0} must be an array.".format(collection)
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                return "{0}[{1}] must be an object.".format(collection, index)
            unknown = sorted(set(item) - allowed)
            if unknown:
                return "Unrecognized {0}[{1}] properties: {2}.".format(
                    collection, index, ", ".join(unknown))
            nulls = sorted(key for key, value in item.items() if value is None)
            if nulls:
                return "Properties must be omitted rather than null: {0}[{1}].{2}".format(
                    collection, index, ",".join(nulls))
        if collection == "tags":
            for index, item in enumerate(items):
                if set(item) != TAG_PROPERTIES:
                    return "tags[{0}] must carry exactly key and value.".format(index)

    return None
