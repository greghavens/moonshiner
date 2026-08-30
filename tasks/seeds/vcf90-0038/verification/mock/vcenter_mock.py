"""PROTECTED FILE -- do not modify.

An in-process loopback stand-in for the vSphere Automation API of a VCF 9.0
vCenter. It listens on 127.0.0.1 on an ephemeral port and serves *only* the five
operations named by the wire contract:

    Cis.Session_create                  POST   /api/session
    Vcenter.Cluster.EvcMode_get         GET    /api/vcenter/cluster/{cluster}/evc-mode
    Vcenter.Cluster.EvcMode_checkSet    POST   /api/vcenter/cluster/{cluster}/evc-mode
    Vcenter.Cluster.EvcMode_set         PUT    /api/vcenter/cluster/{cluster}/evc-mode
    Cis.Tasks_get                       GET    /api/cis/tasks/{task}

Every request that arrives is appended to ``MockVcenter.requests`` verbatim --
method, raw path, raw query string, parsed query, headers and the undecoded
request body -- so a test can assert the exact wire shape a client produced.
Anything that is not one of the five operations above is answered with a vAPI
NotFound error and recorded with ``off_contract=True``.

No VMware endpoint is contacted. The socket never leaves the loopback
interface.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

SESSION_TOKEN = "0f6c8b3d41e94a7fb2c5d0e8a1937f42"
CHECK_TASK_ID = "5c2f9a10-3b7e-4d61-8f0a-2ce41b96d7a3:com.vmware.vcenter.cluster.evc_mode"
SET_TASK_ID = "9e14d7b2-6a03-4c8f-b5d1-70af23e6c904:com.vmware.vcenter.cluster.evc_mode"

# Baseline EVC mode reported by the cluster before anything is changed.
INITIAL_EVC_MODE = {
    "key": "intel-broadwell",
    "masks": [
        {
            "key": "cpuid.MWAIT",
            "name": "cpuid.MWAIT",
            "value": "Val:0x00000001",
        },
        {
            "key": "cpuid.AVX2",
            "name": "cpuid.AVX2",
            "value": "Val:0x00000000",
        },
    ],
}

CLUSTER_ID = "domain-c9"

# Blocking precheck findings returned by the check-set task in the "blocked"
# scenario. Shaped as Vcenter.Cluster.EvcMode.CheckResult.
BLOCKING_CHECK_RESULTS = [
    {
        "error": {
            "error_type": "ERROR",
            "messages": [
                {
                    "id": "com.vmware.vcenter.cluster.evc_mode.host_feature_missing",
                    "default_message": (
                        "Host esx-04.vcf.example does not expose CPU feature "
                        "cpuid.AVX512F required by EVC mode intel-skylake."
                    ),
                    "args": ["esx-04.vcf.example", "cpuid.AVX512F", "intel-skylake"],
                }
            ],
        },
        "host_system": "host-1042",
    },
    {
        "error": {
            "error_type": "ERROR",
            "messages": [
                {
                    "id": "com.vmware.vcenter.cluster.evc_mode.powered_on_vm",
                    "default_message": (
                        "Cluster domain-c9 has powered-on virtual machines that "
                        "would violate EVC mode intel-skylake."
                    ),
                    "args": ["domain-c9", "intel-skylake"],
                }
            ],
        },
    },
]


def _task_info(operation, status, result=None, include_result=True):
    """Build a Cis.Task.Info payload."""
    info = {
        "cancelable": False,
        "description": {
            "id": "com.vmware.vcenter.cluster.evc_mode.%s" % operation,
            "default_message": "EVC mode %s on cluster %s" % (operation, CLUSTER_ID),
            "args": [],
        },
        "service": "com.vmware.vcenter.cluster.evc_mode",
        "operation": operation,
        "status": status,
    }
    if result is not None and include_result:
        info["result"] = result
    return info


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, *args):  # silence stderr chatter
        pass

    @property
    def mock(self):
        return self.server.mock

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return ""
        try:
            count = int(length)
        except ValueError:
            return ""
        if count <= 0:
            return ""
        return self.rfile.read(count).decode("utf-8", "replace")

    def _send(self, status, payload):
        if payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _record(self, body, off_contract=False):
        split = urlsplit(self.path)
        entry = {
            "method": self.command,
            # Percent-escaping a path segment is legal HTTP, so routing and
            # assertions both work on the decoded path.
            "path": unquote(split.path),
            "raw_path": split.path,
            "query": split.query,
            "query_params": parse_qs(split.query, keep_blank_values=True),
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
            "off_contract": off_contract,
        }
        with self.mock.lock:
            self.mock.requests.append(entry)
        return entry

    def _not_found(self, body):
        self._record(body, off_contract=True)
        self._send(
            404,
            {
                "error_type": "NOT_FOUND",
                "messages": [
                    {
                        "id": "com.vmware.vapi.rest.httpNotFound",
                        "default_message": "Operation %s %s is not served by this fixture."
                        % (self.command, self.path),
                        "args": [self.command, self.path],
                    }
                ],
            },
        )

    def _unauthenticated(self, message):
        self._send(
            401,
            {
                "error_type": "UNAUTHENTICATED",
                "messages": [
                    {
                        "id": "com.vmware.vapi.endpoint.method.authentication.required",
                        "default_message": message,
                        "args": [],
                    }
                ],
            },
        )

    def _requires_session(self, entry):
        """True when the caller presented the session token header."""
        return entry["headers"].get("vmware-api-session-id") == SESSION_TOKEN

    # -- routing ----------------------------------------------------------
    def _dispatch(self):
        body = self._read_body()
        split = urlsplit(self.path)
        path = unquote(split.path)
        query = parse_qs(split.query, keep_blank_values=True)
        mock = self.mock

        # Cis.Session_create -- POST /api/session, basic_auth
        if path == "/api/session" and self.command == "POST":
            entry = self._record(body)
            auth = entry["headers"].get("authorization", "")
            if not auth.lower().startswith("basic "):
                self._unauthenticated("Basic authentication is required.")
                return
            try:
                decoded = base64.b64decode(auth.split(None, 1)[1]).decode("utf-8")
            except Exception:
                self._unauthenticated("Malformed Basic credentials.")
                return
            if ":" not in decoded:
                self._unauthenticated("Malformed Basic credentials.")
                return
            self._send(201, SESSION_TOKEN)
            return

        evc_path = "/api/vcenter/cluster/%s/evc-mode" % CLUSTER_ID

        # Vcenter.Cluster.EvcMode_get / _checkSet / _set
        if path == evc_path:
            entry = self._record(body)
            if not self._requires_session(entry):
                self._unauthenticated("A session token is required.")
                return

            if self.command == "GET":
                mock.reads += 1
                self._send(200, dict(mock.state))
                return

            if self.command == "POST" and query.get("action") == ["check-set"]:
                mock.check_polls = 0
                self._send(202, CHECK_TASK_ID)
                return

            if self.command == "PUT" and "action" not in query:
                mock.set_polls = 0
                mock.set_bodies.append(body)
                try:
                    spec = json.loads(body) if body else {}
                except ValueError:
                    spec = {}
                # A SetSpec without evc_mode clears the cluster's EVC mode.
                if isinstance(spec, dict) and spec.get("evc_mode") is not None:
                    mock.pending_state = {"evc_mode": spec["evc_mode"]}
                else:
                    mock.pending_state = {}
                self._send(202, SET_TASK_ID)
                return

            self._not_found(body)
            return

        # Cis.Tasks_get -- GET /api/cis/tasks/{task}
        if path.startswith("/api/cis/tasks/") and self.command == "GET":
            entry = self._record(body)
            if not self._requires_session(entry):
                self._unauthenticated("A session token is required.")
                return
            task = path[len("/api/cis/tasks/") :]
            include_result = query.get("exclude_result", ["false"])[0].lower() != "true"

            if task == CHECK_TASK_ID:
                mock.check_polls += 1
                if mock.check_polls < mock.polls_before_terminal:
                    self._send(200, _task_info("check-set", "RUNNING"))
                    return
                if mock.scenario == "precheck_task_failed":
                    self._send(200, _task_info("check-set", "FAILED"))
                    return
                result = (
                    BLOCKING_CHECK_RESULTS
                    if mock.scenario == "precheck_blocked"
                    else None
                )
                self._send(
                    200,
                    _task_info("check-set", "SUCCEEDED", result, include_result),
                )
                return

            if task == SET_TASK_ID:
                mock.set_polls += 1
                if mock.set_polls < mock.polls_before_terminal:
                    self._send(200, _task_info("set", "RUNNING"))
                    return
                if mock.scenario == "set_task_failed":
                    mock.pending_state = None
                    self._send(200, _task_info("set", "FAILED"))
                    return
                if mock.pending_state is not None:
                    mock.state = mock.pending_state
                    mock.pending_state = None
                self._send(200, _task_info("set", "SUCCEEDED"))
                return

            self._not_found(body)
            return

        self._not_found(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch


class MockVcenter:
    """Loopback fixture for the five contracted vSphere Automation operations.

    ``scenario`` selects what the check-set task reports when it reaches a
    terminal state:

    ``precheck_clean``        SUCCEEDED with no result -- the mutation may proceed.
    ``precheck_blocked``      SUCCEEDED with a non-empty list of CheckResult errors.
    ``precheck_task_failed``  FAILED.
    ``set_task_failed``       Clean precheck followed by a failed set task.
    """

    def __init__(self, scenario="precheck_clean", polls_before_terminal=2):
        self.scenario = scenario
        self.polls_before_terminal = polls_before_terminal
        self.requests = []
        self.set_bodies = []
        self.state = {"evc_mode": json.loads(json.dumps(INITIAL_EVC_MODE))}
        self.check_polls = 0
        self.set_polls = 0
        self.reads = 0
        self.pending_state = None
        self.lock = threading.Lock()
        self._server = None
        self._thread = None

    def start(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.mock = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def port(self):
        return self._server.server_address[1]

    # -- helpers for tests ------------------------------------------------
    def on_contract(self):
        return [r for r in self.requests if not r["off_contract"]]

    def off_contract(self):
        return [r for r in self.requests if r["off_contract"]]

    def matching(self, method, path_suffix):
        return [
            r
            for r in self.requests
            if r["method"] == method and r["path"].endswith(path_suffix)
        ]

    def evc_mode_writes(self):
        return [
            r
            for r in self.requests
            if r["method"] == "PUT" and r["path"].endswith("/evc-mode")
        ]
