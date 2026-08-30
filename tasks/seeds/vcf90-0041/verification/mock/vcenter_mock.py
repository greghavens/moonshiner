#!/usr/bin/env python3
"""Loopback mock of the vCenter subset described by docs/contract.json.

The mock is pinned to the contract: routes, security schemes, query parameters
and request-body properties are all read from that file at start up, and any
request that does not match an operation the contract names is rejected.  It is
a stand-in for a vCenter appliance so that the right-sizing tool can be
exercised without touching a live VMware endpoint.

Every request - accepted or rejected - is appended to a JSON Lines request log
so a test can inspect the exact wire shape that a client produced.

Usage:

    python3 mock/vcenter_mock.py \
        --contract docs/contract.json \
        --seed fixtures/vcenter_state.json \
        --log /tmp/requests.jsonl \
        --state-out /tmp/state.json \
        --port-file /tmp/port

With ``--port 0`` (the default) the OS picks a free loopback port and the mock
writes it to ``--port-file`` and to stdout as ``LISTENING <port>``.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import re
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

CONTENT_TYPE_JSON = "application/json"


def _message(msg_id, default_message, args=None):
    return {
        "id": msg_id,
        "default_message": default_message,
        "args": list(args or []),
    }


def _error(error_type, msg_id, default_message, args=None, **extra):
    body = {
        "error_type": error_type,
        "messages": [_message(msg_id, default_message, args)],
    }
    body.update(extra)
    return body


class Contract:
    """The subset of the vSphere Automation API this mock is allowed to serve."""

    def __init__(self, document):
        self.document = document
        self.base_path = document["server"]["base_path"].rstrip("/")
        self.schemes = document["security_schemes"]
        self.schemas = document["schemas"]
        self.routes = []
        for operation_id, spec in document["operations"].items():
            self.routes.append((self._compile(spec["path"]), operation_id, spec))

    def _compile(self, path):
        pattern = "^" + re.escape(self.base_path)
        for chunk in re.split(r"(\{[^}]+\})", path):
            if chunk.startswith("{") and chunk.endswith("}"):
                pattern += "(?P<%s>[^/]+)" % chunk[1:-1]
            else:
                pattern += re.escape(chunk)
        return re.compile(pattern + "$")

    def match(self, method, path):
        """Return ``(operation_id, spec, path_params)`` or ``(None, None, None)``."""
        for pattern, operation_id, spec in self.routes:
            match = pattern.match(path)
            if match and spec["method"] == method:
                return operation_id, spec, match.groupdict()
        return None, None, None

    def body_properties(self, spec):
        body = spec.get("request_body")
        if not body:
            return None
        return self.schemas[body["schema"]["$ref"]]["properties"]


class Inventory:
    """Mutable service state, seeded from a fixture on every start."""

    def __init__(self, seed):
        self.username = seed["credentials"]["username"]
        self.password = seed["credentials"]["password"]
        policy = seed["session_policy"]
        self.token_max_uses = list(policy["token_max_uses"])
        self.default_token_max_uses = policy["default_token_max_uses"]
        self.token_prefix = policy["token_prefix"]
        self.vms = {vm["vm"]: copy.deepcopy(vm) for vm in seed["virtual_machines"]}
        self.tokens = {}
        self.tokens_issued = []
        self.mutations = []

    def issue_token(self):
        index = len(self.tokens_issued)
        token = "%s-%d" % (self.token_prefix, index + 1)
        if index < len(self.token_max_uses):
            max_uses = self.token_max_uses[index]
        else:
            max_uses = self.default_token_max_uses
        self.tokens[token] = {"remaining_uses": max_uses, "revoked": False}
        self.tokens_issued.append(token)
        return token

    def spend_token(self, token):
        """Return ``True`` when the token was live and one use was consumed."""
        state = self.tokens.get(token)
        if state is None or state["revoked"] or state["remaining_uses"] <= 0:
            return False
        state["remaining_uses"] -= 1
        return True

    def revoke_token(self, token):
        self.tokens[token]["revoked"] = True

    def by_name(self, name):
        for vm in self.vms.values():
            if vm["name"] == name:
                return vm
        return None

    def snapshot(self):
        return {
            "tokens_issued": list(self.tokens_issued),
            "tokens": copy.deepcopy(self.tokens),
            "mutations": copy.deepcopy(self.mutations),
            "virtual_machines": [
                copy.deepcopy(self.vms[key]) for key in sorted(self.vms)
            ],
        }


class Recorder:
    def __init__(self, log_path, state_path, inventory):
        self.log_path = log_path
        self.state_path = state_path
        self.inventory = inventory
        self.lock = threading.Lock()
        self.seq = 0
        with open(self.log_path, "w", encoding="utf-8"):
            pass
        self.write_state()

    def record(self, entry):
        with self.lock:
            self.seq += 1
            entry["seq"] = self.seq
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._write_state_locked()

    def write_state(self):
        with self.lock:
            self._write_state_locked()

    def _write_state_locked(self):
        payload = self.inventory.snapshot()
        payload["requests_recorded"] = self.seq
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.state_path)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcenter-mock/1.0"
    sys_version = ""

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # pragma: no cover - keep stderr quiet
        pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_OPTIONS(self):
        self._dispatch("OPTIONS")

    @property
    def contract(self):
        return self.server.contract

    @property
    def inventory(self):
        return self.server.inventory

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            return self.rfile.read(int(length))
        except ValueError:
            return b""

    def _respond(self, status, payload, entry):
        body = b""
        headers = []
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers.append(("Content-Type", CONTENT_TYPE_JSON))
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        entry["status"] = status
        entry["response_body"] = payload
        self.server.recorder.record(entry)

    # -- request handling -------------------------------------------------

    def _dispatch(self, method):
        split = urlsplit(self.path)
        raw_query = split.query
        query = {}
        for key, value in parse_qsl(raw_query, keep_blank_values=True):
            query.setdefault(key, []).append(value)
        raw_body = self._read_body()
        try:
            body_text = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            body_text = None
        body_json = None
        body_json_valid = False
        if body_text:
            try:
                body_json = json.loads(body_text)
                body_json_valid = True
            except ValueError:
                body_json_valid = False

        operation_id, spec, path_params = self.contract.match(method, split.path)
        entry = {
            "method": method,
            "path": split.path,
            "raw_query": raw_query,
            "query": query,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body_raw": body_text,
            "body_json": body_json if body_json_valid else None,
            "operation_id": operation_id,
            "path_params": path_params or {},
        }

        if operation_id is None:
            self._respond(
                404,
                _error(
                    "OPERATION_NOT_FOUND",
                    "com.vmware.vapi.rest.operation_not_found",
                    "The endpoint %s %s is not part of the pinned contract."
                    % (method, split.path),
                    [method, split.path],
                ),
                entry,
            )
            return

        token = self.headers.get(self.contract.schemes["api_key_auth"]["name"])
        if spec["security"] == "basic_auth":
            if not self._check_basic_auth(entry):
                return
        else:
            if not self._check_session_auth(token, entry):
                return

        handler = {
            "Cis.Session_create": self._session_create,
            "Cis.Session_delete": self._session_delete,
            "Vcenter.VM_list": self._vm_list,
            "Vcenter.Vm.Hardware.Cpu_update": self._cpu_update,
            "Vcenter.Vm.Hardware.Memory_update": self._memory_update,
        }[operation_id]
        handler(
            entry,
            spec=spec,
            query=query,
            path_params=path_params,
            token=token,
            body_text=body_text,
            body_json=body_json,
            body_json_valid=body_json_valid,
        )

    def _unauthenticated(self, entry, detail):
        self._respond(
            401,
            _error(
                "UNAUTHENTICATED",
                "com.vmware.vapi.endpoint.method.authentication.required",
                detail,
                challenge='Basic realm="vCenter"',
            ),
            entry,
        )

    def _check_basic_auth(self, entry):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            self._unauthenticated(entry, "HTTP Basic credentials are required.")
            return False
        try:
            decoded = base64.b64decode(header[len("Basic "):], validate=True)
            username, _, password = decoded.decode("utf-8").partition(":")
        except Exception:
            self._unauthenticated(entry, "The Authorization header is malformed.")
            return False
        if username != self.inventory.username or password != self.inventory.password:
            self._unauthenticated(entry, "The username and password are invalid.")
            return False
        return True

    def _check_session_auth(self, token, entry):
        if not token:
            self._unauthenticated(
                entry, "The session identifier is missing from the request."
            )
            return False
        if not self.inventory.spend_token(token):
            self._unauthenticated(
                entry,
                "The session identifier in the request's security context "
                "identifies a session that has expired.",
            )
            return False
        return True

    # -- operations -------------------------------------------------------

    def _session_create(self, entry, **kwargs):
        token = self.inventory.issue_token()
        self._respond(201, token, entry)

    def _session_delete(self, entry, token=None, **kwargs):
        self.inventory.revoke_token(token)
        self._respond(204, None, entry)

    def _vm_list(self, entry, spec=None, query=None, **kwargs):
        allowed = {p["name"]: p for p in spec["query_parameters"]}
        for name in query:
            if name not in allowed:
                self._respond(
                    400,
                    _error(
                        "UNEXPECTED_INPUT",
                        "com.vmware.vapi.rest.unexpected_parameter",
                        "Vcenter.VM_list does not define the query parameter %s."
                        % name,
                        [name],
                    ),
                    entry,
                )
                return
        for name, values in query.items():
            if allowed[name].get("unique_items") and len(set(values)) != len(values):
                self._respond(
                    400,
                    _error(
                        "INVALID_ARGUMENT",
                        "com.vmware.vapi.rest.duplicate_value",
                        "The %s filter must not repeat a value." % name,
                        [name],
                    ),
                    entry,
                )
                return
        states = allowed["power_states"]["schema"]["items"]["enum"]
        for value in query.get("power_states", []):
            if value not in states:
                self._respond(
                    400,
                    _error(
                        "INVALID_ARGUMENT",
                        "com.vmware.vapi.rest.invalid_enum",
                        "%s is not a supported power state." % value,
                        [value],
                    ),
                    entry,
                )
                return

        summaries = []
        for key in sorted(self.inventory.vms):
            vm = self.inventory.vms[key]
            if "vms" in query and vm["vm"] not in query["vms"]:
                continue
            if "names" in query and vm["name"] not in query["names"]:
                continue
            if "power_states" in query and vm["power_state"] not in query["power_states"]:
                continue
            summaries.append(
                {
                    "vm": vm["vm"],
                    "name": vm["name"],
                    "power_state": vm["power_state"],
                    "cpu_count": vm["cpu"]["count"],
                    "memory_size_mib": vm["memory"]["size_mib"],
                }
            )
        self._respond(200, summaries, entry)

    def _update(self, entry, spec, path_params, body_text, body_json, body_json_valid,
                section, hot_add_requires):
        vm = self.inventory.vms.get(path_params["vm"])
        if vm is None:
            self._respond(
                404,
                _error(
                    "NOT_FOUND",
                    "com.vmware.vapi.vcenter.vm.not_found",
                    "The virtual machine %s was not found." % path_params["vm"],
                    [path_params["vm"]],
                ),
                entry,
            )
            return
        media_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if media_type != CONTENT_TYPE_JSON:
            self._respond(
                400,
                _error(
                    "UNEXPECTED_INPUT",
                    "com.vmware.vapi.rest.unsupported_media_type",
                    "The request body must be sent as %s." % CONTENT_TYPE_JSON,
                    [CONTENT_TYPE_JSON],
                ),
                entry,
            )
            return
        if not body_text or not body_json_valid or not isinstance(body_json, dict):
            self._respond(
                400,
                _error(
                    "INVALID_ARGUMENT",
                    "com.vmware.vapi.rest.invalid_body",
                    "The request body must be a JSON object holding the update spec.",
                ),
                entry,
            )
            return

        properties = self.contract.body_properties(spec)
        for name in body_json:
            if name not in properties:
                self._respond(
                    400,
                    _error(
                        "UNEXPECTED_INPUT",
                        "com.vmware.vapi.rest.unexpected_property",
                        "%s is not a property of the update spec." % name,
                        [name],
                    ),
                    entry,
                )
                return
        expected_python = {"integer": int, "boolean": bool}
        for name, value in body_json.items():
            if value is None:
                # The specification documents every update-spec property as
                # "if missing or null, the value is unchanged".
                continue
            wanted = expected_python[properties[name]["type"]]
            if isinstance(value, bool) != (wanted is bool) or not isinstance(value, wanted):
                self._respond(
                    400,
                    _error(
                        "INVALID_ARGUMENT",
                        "com.vmware.vapi.rest.invalid_type",
                        "%s must be of type %s." % (name, properties[name]["type"]),
                        [name, properties[name]["type"]],
                    ),
                    entry,
                )
                return
        for name in ("hot_add_enabled", "hot_remove_enabled"):
            if body_json.get(name) is None:
                continue
            if vm["power_state"] in hot_add_requires:
                continue
            self._respond(
                400,
                _error(
                    "NOT_ALLOWED_IN_CURRENT_STATE",
                    "com.vmware.vapi.vcenter.vm.hardware.hot_plug_state",
                    "%s may not be modified while the virtual machine is %s."
                    % (name, vm["power_state"]),
                    [name, vm["power_state"]],
                ),
                entry,
            )
            return

        changed = {}
        for name, value in body_json.items():
            if value is None:
                continue
            vm[section][name] = value
            changed[name] = value
        self.inventory.mutations.append(
            {
                "operation_id": entry["operation_id"],
                "vm": vm["vm"],
                "name": vm["name"],
                "changed": changed,
            }
        )
        self._respond(204, None, entry)

    def _cpu_update(self, entry, spec=None, path_params=None, body_text=None,
                    body_json=None, body_json_valid=False, **kwargs):
        self._update(
            entry,
            spec,
            path_params,
            body_text,
            body_json,
            body_json_valid,
            section="cpu",
            hot_add_requires=("POWERED_OFF",),
        )

    def _memory_update(self, entry, spec=None, path_params=None, body_text=None,
                       body_json=None, body_json_valid=False, **kwargs):
        self._update(
            entry,
            spec,
            path_params,
            body_text,
            body_json,
            body_json_valid,
            section="memory",
            hot_add_requires=("POWERED_OFF", "SUSPENDED"),
        )


def build_server(contract_path, seed_path, log_path, state_path, host, port):
    with open(contract_path, encoding="utf-8") as handle:
        contract = Contract(json.load(handle))
    with open(seed_path, encoding="utf-8") as handle:
        inventory = Inventory(json.load(handle))
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.contract = contract
    httpd.inventory = inventory
    httpd.recorder = Recorder(log_path, state_path, inventory)
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--contract", default=os.path.join(here, "docs", "contract.json"))
    parser.add_argument("--seed", default=os.path.join(here, "fixtures", "vcenter_state.json"))
    parser.add_argument("--log", required=True, help="JSON Lines request log to write")
    parser.add_argument("--state-out", required=True, help="JSON state snapshot to write")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file")
    args = parser.parse_args(argv)

    httpd = build_server(
        args.contract, args.seed, args.log, args.state_out, args.host, args.port
    )
    port = httpd.server_address[1]
    if args.port_file:
        tmp = args.port_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(str(port))
        os.replace(tmp, args.port_file)
    sys.stdout.write("LISTENING %d\n" % port)
    sys.stdout.flush()

    def shutdown(signum, frame):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        httpd.serve_forever(poll_interval=0.1)
    finally:
        httpd.recorder.write_state()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
