#!/usr/bin/env python3
"""Deterministic protected verifier for vcf90-0014.

Checks that docs/contract.json is still the 9.0.0.0 projection, that the client
package is stdlib-only, that the precheck genuinely gates the mutating call, and
that both request bodies carry the exact HostCommissionSpec wire shape with every
unset optional member omitted rather than sent empty. No live VMware endpoint is
contacted; the client only ever talks to a loopback mock on 127.0.0.1.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import secrets
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = "vcf_host_commission"
MODULE_PATH = SRC / PACKAGE / "commission.py"
INIT_PATH = SRC / PACKAGE / "__init__.py"
PROJECT_PATH = ROOT / "pyproject.toml"
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / ".protected" / "mock_sddc_manager.py"

COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
TAG = "9.0.0.0"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
OPERATION_IDS = [
    "validateHostCommissionSpec",
    "getHostCommissionValidationByID",
    "commissionHosts",
]
ROUTES = [
    ("validateHostCommissionSpec", "POST", "/v1/hosts/validations"),
    ("getHostCommissionValidationByID", "GET", "/v1/hosts/validations/{id}"),
    ("commissionHosts", "POST", "/v1/hosts"),
]
POINTERS = {
    "validateHostCommissionSpec": "/paths/~1v1~1hosts~1validations/post/operationId",
    "getHostCommissionValidationByID": "/paths/~1v1~1hosts~1validations~1{id}/get/operationId",
    "commissionHosts": "/paths/~1v1~1hosts/post/operationId",
}
# Declaration order of HostCommissionSpec.properties at the pinned commit.
SPEC_MEMBER_ORDER = [
    "fqdn",
    "username",
    "password",
    "storageType",
    "vvolStorageProtocolType",
    "networkPoolId",
    "networkPoolName",
    "sshThumbprint",
    "sslThumbprint",
]
SPEC_REQUIRED = ["fqdn", "networkPoolId", "password", "storageType", "username"]
SPEC_OPTIONAL = [
    "networkPoolName",
    "sshThumbprint",
    "sslThumbprint",
    "vvolStorageProtocolType",
]
# Present in the 9.0.0.0 revision; the 9.1.0.0 revision appends PENDING.
CHECK_RESULT_STATUS_9_0 = [
    "IN_PROGRESS",
    "SUCCEEDED",
    "FAILED",
    "SKIPPED",
    "CANCELLED",
    "CANCELLATION_IN_PROGRESS",
]
EXECUTION_STATUS_9_0 = [
    "IN_PROGRESS",
    "FAILED",
    "COMPLETED",
    "UNKNOWN",
    "SKIPPED",
    "CANCELLED",
    "CANCELLATION_IN_PROGRESS",
]
RESULT_STATUS_9_0 = [
    "SUCCEEDED",
    "FAILED",
    "WARNING",
    "UNKNOWN",
    "CANCELLATION_IN_PROGRESS",
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# contract provenance
# --------------------------------------------------------------------------- #


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract.get("source", {})
    require(
        contract.get("contractFormat") == "focused-openapi-projection-v1",
        "contract format changed",
    )
    require(source.get("repository") == "vmware/vcf-api-specs", "repository changed")
    require(
        source.get("repositoryCommitSha") == COMMIT,
        "contract commit is not the 9.0.0.0 tag",
    )
    require(source.get("repositoryTag") == TAG, "contract tag changed")
    require(source.get("specPath") == SPEC_PATH, "contract spec path changed")
    require(source.get("license") == "Apache-2.0", "contract license changed")
    require(source.get("openapi") == "3.0.1", "OpenAPI version changed")
    require(source.get("apiVersion") == TAG, "contract API version is not 9.0.0.0")

    operations = contract.get("operations", [])
    require(
        [item.get("operationId") for item in operations] == OPERATION_IDS,
        "contract must name exactly the three pinned operationIds",
    )
    require(
        [(i.get("operationId"), i.get("method"), i.get("path")) for i in operations]
        == ROUTES,
        "contract route projection changed",
    )
    by_id = {item["operationId"]: item for item in operations}
    require(
        all(item.get("deprecated") is False for item in operations),
        "every contracted operation must be non-deprecated at this commit",
    )
    for operation_id in ("validateHostCommissionSpec", "commissionHosts"):
        body = by_id[operation_id].get("requestBody") or {}
        require(body.get("required") is True, f"{operation_id} request body is required")
        require(
            body.get("contentType") == "application/json",
            f"{operation_id} content type changed",
        )
        require(
            body.get("schema") == {"type": "array", "items": "HostCommissionSpec"},
            f"{operation_id} takes an array of HostCommissionSpec",
        )
        require(
            by_id[operation_id]["responses"]["202"]["schema"]
            == ("Validation" if operation_id == "validateHostCommissionSpec" else "Task"),
            f"{operation_id} 202 schema changed",
        )
    poll = by_id["getHostCommissionValidationByID"]
    require(poll.get("requestBody") is None, "the validation poll takes no request body")
    require(
        [(p.get("name"), p.get("in"), p.get("required")) for p in poll.get("parameters", [])]
        == [("id", "path", True)],
        "the validation poll takes exactly one required path parameter",
    )
    require(
        poll["responses"]["202"]["schema"] == "Validation",
        "the validation poll 202 schema changed",
    )

    spec_schema = contract.get("schemas", {}).get("HostCommissionSpec", {})
    require(
        spec_schema.get("propertyOrder") == SPEC_MEMBER_ORDER,
        "HostCommissionSpec member order changed",
    )
    require(
        spec_schema.get("required") == SPEC_REQUIRED,
        "HostCommissionSpec required members changed",
    )
    vocabulary = contract.get("statusVocabulary", {})
    require(
        vocabulary.get("validationExecutionStatus") == EXECUTION_STATUS_9_0,
        "Validation.executionStatus vocabulary changed",
    )
    require(
        vocabulary.get("validationResultStatus") == RESULT_STATUS_9_0,
        "Validation.resultStatus vocabulary changed",
    )
    require(
        vocabulary.get("validationCheckResultStatus") == CHECK_RESULT_STATUS_9_0,
        "ValidationCheck.resultStatus vocabulary is not the 9.0.0.0 revision",
    )
    require(
        "nestedValidationChecks"
        not in contract["schemas"].get("ValidationCheck", {}).get("properties", {}),
        "ValidationCheck carries a member the 9.0.0.0 revision does not define",
    )

    require(sources.get("repository") == "vmware/vcf-api-specs", "source repository changed")
    require(
        sources.get("repositoryCommitSha") == COMMIT,
        "source commit is not the 9.0.0.0 tag",
    )
    require(sources.get("repositoryTag") == TAG, "source tag changed")
    require(sources.get("specPath") == SPEC_PATH, "source spec path changed")
    require(sources.get("license") == "Apache-2.0", "source license changed")
    require(sources.get("operationIds") == OPERATION_IDS, "source operationIds changed")
    require(
        COMMIT in sources.get("specUrl", "") and sources["specUrl"].endswith(SPEC_PATH),
        "official spec URL must be immutable",
    )
    require(
        [
            (
                item.get("operationId"),
                item.get("method"),
                item.get("path"),
                item.get("specJsonPointer"),
                item.get("repositoryCommitSha"),
                item.get("specPath"),
            )
            for item in sources.get("operations", [])
        ]
        == [
            (operation_id, method, path, POINTERS[operation_id], COMMIT, SPEC_PATH)
            for operation_id, method, path in ROUTES
        ],
        "each operation must repeat its exact pinned source",
    )
    require(
        sources.get("derivation", {}).get("documentationPageUsedAsContractSource") is False,
        "a documentation page must not be the contract source",
    )


# --------------------------------------------------------------------------- #
# package shape
# --------------------------------------------------------------------------- #


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def verify_package_shape() -> None:
    require(MODULE_PATH.is_file(), f"src/{PACKAGE}/commission.py is missing")
    require(INIT_PATH.is_file(), "protected package initializer is missing")
    project = tomllib.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    require(
        project.get("project", {}).get("dependencies") == [], "dependencies must be empty"
    )
    require(
        project.get("tool", {}).get("moonshiner", {}).get("stdlib-only") is True,
        "package must remain stdlib-only",
    )

    source = MODULE_PATH.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(MODULE_PATH))
    except SyntaxError as error:
        raise VerificationError(f"commission.py is not valid Python: {error.msg}") from None
    roots = imported_roots(tree)
    third_party = roots - set(sys.stdlib_module_names) - {"__future__", PACKAGE}
    require(
        not third_party,
        "commission.py imports non-stdlib modules: " + ", ".join(sorted(third_party)),
    )
    urllib_request_imported = any(
        (
            isinstance(node, ast.Import)
            and any(alias.name == "urllib.request" for alias in node.names)
        )
        or (
            isinstance(node, ast.ImportFrom)
            and (
                node.module == "urllib.request"
                or (
                    node.module == "urllib"
                    and any(alias.name == "request" for alias in node.names)
                )
            )
        )
        for node in ast.walk(tree)
    )
    require(urllib_request_imported, "commission.py must use urllib.request")
    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix.casefold()
        in {".whl", ".egg", ".zip", ".so", ".dll", ".dylib", ".pyc"}
    ]
    require(not vendored, "the package must not vendor dependencies or binary artifacts")


def verify_public_signatures(module: Any) -> None:
    """Protect the constructor surfaces the ticket gives its callers."""
    empty = inspect.Parameter.empty
    host_parameters = list(inspect.signature(module.HostCommissionSpec).parameters.values())
    require(
        [(item.name, item.kind, item.default) for item in host_parameters]
        == [
            ("fqdn", inspect.Parameter.POSITIONAL_OR_KEYWORD, empty),
            ("username", inspect.Parameter.POSITIONAL_OR_KEYWORD, empty),
            ("password", inspect.Parameter.POSITIONAL_OR_KEYWORD, empty),
            ("storage_type", inspect.Parameter.POSITIONAL_OR_KEYWORD, empty),
            ("network_pool_id", inspect.Parameter.POSITIONAL_OR_KEYWORD, empty),
            ("vvol_storage_protocol_type", inspect.Parameter.POSITIONAL_OR_KEYWORD, None),
            ("network_pool_name", inspect.Parameter.POSITIONAL_OR_KEYWORD, None),
            ("ssh_thumbprint", inspect.Parameter.POSITIONAL_OR_KEYWORD, None),
            ("ssl_thumbprint", inspect.Parameter.POSITIONAL_OR_KEYWORD, None),
        ],
        "HostCommissionSpec constructor does not match the public surface",
    )

    client_parameters = list(inspect.signature(module.SddcManagerClient).parameters.values())
    require(
        [(item.name, item.kind, item.default) for item in client_parameters]
        == [
            ("base_url", inspect.Parameter.POSITIONAL_OR_KEYWORD, empty),
            ("token", inspect.Parameter.POSITIONAL_OR_KEYWORD, empty),
            ("timeout", inspect.Parameter.KEYWORD_ONLY, 30.0),
            ("poll_interval", inspect.Parameter.KEYWORD_ONLY, 5.0),
            ("max_polls", inspect.Parameter.KEYWORD_ONLY, 60),
            ("sleep", inspect.Parameter.KEYWORD_ONLY, None),
        ],
        "SddcManagerClient constructor does not match the public surface",
    )


# --------------------------------------------------------------------------- #
# mock harness
# --------------------------------------------------------------------------- #


def wait_for_ready(ready_path: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(
                "mock exited before readiness: " + (stderr or stdout or "no diagnostics")
            )
        if ready_path.is_file():
            try:
                ready = load_json(ready_path)
                require(ready.get("host") == "127.0.0.1", "mock is not loopback-only")
                require(
                    ready.get("operationIds") == OPERATION_IDS,
                    "mock operation set changed",
                )
                port = ready.get("port")
                if isinstance(port, int) and 0 < port < 65536:
                    return port
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.02)
    raise VerificationError("mock did not become ready")


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def failed_check(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "severity": "ERROR",
        "resultStatus": "FAILED",
        "errorResponse": {
            "errorCode": "HOST_COMMISSION_PRECHECK_FAILED",
            "errorType": "VALIDATION_FAILED",
            "message": description,
            "referenceToken": "PRECHECK-REF",
        },
    }


class Case:
    """One mock lifetime: a scenario, a client call, and the resulting request log."""

    def __init__(self, name: str, scenario: dict[str, Any]) -> None:
        self.name = name
        self.token = secrets.token_urlsafe(24)
        self.validation_id = f"validation-{secrets.token_hex(6)}"
        self.task_id = f"task-{secrets.token_hex(6)}"
        self.password = secrets.token_urlsafe(18)
        self.network_pool_id = f"pool-{secrets.token_hex(4)}"
        self.fqdn = f"esx-{secrets.token_hex(4)}.lab.local"
        self.scenario = {
            "accessToken": self.token,
            "validationId": self.validation_id,
            "taskId": self.task_id,
            "pollsBeforeTerminal": 1,
            "executionStatus": "COMPLETED",
            "resultStatus": "SUCCEEDED",
            "validationChecks": [],
            **scenario,
        }
        self.entries: list[dict[str, Any]] = []
        self.sleeps: list[float] = []
        self.outcome: Any = None
        self.error: BaseException | None = None

    # -- running ---------------------------------------------------------- #

    def specs(self, module: Any) -> list[Any]:
        return [
            module.HostCommissionSpec(
                fqdn=self.fqdn,
                username="root",
                password=self.password,
                storage_type="VSAN_ESA",
                network_pool_id=self.network_pool_id,
            )
        ]

    def run(self, module: Any, make_specs: Any = None) -> None:
        with tempfile.TemporaryDirectory(prefix="vcf90-0014-") as directory:
            workspace = Path(directory)
            scenario_path = workspace / "scenario.json"
            log_path = workspace / "requests.jsonl"
            ready_path = workspace / "ready.json"
            scenario_path.write_text(json.dumps(self.scenario), encoding="utf-8")
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(MOCK_PATH),
                    "--contract",
                    str(CONTRACT_PATH),
                    "--scenario",
                    str(scenario_path),
                    "--request-log",
                    str(log_path),
                    "--ready-file",
                    str(ready_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                port = wait_for_ready(ready_path, process)
                try:
                    client = module.SddcManagerClient(
                        f"http://127.0.0.1:{port}",
                        self.token,
                        poll_interval=7.5,
                        max_polls=5,
                        sleep=self.record_sleep,
                    )
                    specs = (make_specs or self.specs)(module)
                    self.outcome = client.commission_after_precheck(specs)
                except Exception as error:  # noqa: BLE001
                    self.error = error
            finally:
                stop_process(process)
            if log_path.is_file():
                self.entries = [
                    json.loads(line)
                    for line in log_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]

    def record_sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    # -- helpers ---------------------------------------------------------- #

    def check(self, condition: bool, message: str) -> None:
        require(condition, f"[{self.name}] {message}")

    def operation_sequence(self) -> list[str | None]:
        return [entry["operationId"] for entry in self.entries]

    def of(self, operation_id: str) -> list[dict[str, Any]]:
        return [entry for entry in self.entries if entry["operationId"] == operation_id]

    def commission_effect_total(self) -> int:
        return self.entries[-1]["commissionEffectTotal"] if self.entries else 0

    def commissioned_fqdns(self) -> list[str]:
        return self.entries[-1]["commissionedFqdns"] if self.entries else []

    def header(self, entry: dict[str, Any], name: str) -> list[str]:
        return entry["headerValues"].get(name, [])

    def check_transport(self, entry: dict[str, Any]) -> None:
        label = entry["operationId"]
        self.check(
            self.header(entry, "authorization") == [f"Bearer {self.token}"],
            f"{label} must send exactly one bearer Authorization header",
        )
        self.check(
            [value.strip() for value in self.header(entry, "accept")]
            == ["application/json"],
            f"{label} must accept application/json",
        )
        self.check(entry["rawQuery"] == "", f"{label} must not send query parameters")
        self.check(
            entry["responseStatus"] == 202,
            f"{label} was answered {entry['responseStatus']}, so the wire shape was rejected",
        )

    def check_spec_body(self, entry: dict[str, Any]) -> list[list[tuple[str, Any]]]:
        """The body must be a JSON array of specification-ordered string members."""
        label = entry["operationId"]
        content_types = [
            value.split(";", 1)[0].strip().casefold()
            for value in self.header(entry, "content-type")
        ]
        self.check(
            content_types == ["application/json"],
            f"{label} must declare exactly one application/json Content-Type",
        )
        try:
            payload = json.loads(entry["body"], object_pairs_hook=lambda pairs: pairs)
        except json.JSONDecodeError:
            raise VerificationError(f"[{self.name}] {label} body is not JSON") from None
        self.check(
            isinstance(payload, list),
            f"{label} must send a bare JSON array, not a wrapper object",
        )
        for index, element in enumerate(payload):
            self.check(
                isinstance(element, list),
                f"{label} array element {index} is not a JSON object",
            )
            names = [name for name, _ in element]
            self.check(
                all(name in SPEC_MEMBER_ORDER for name in names),
                f"{label} element {index} carries members outside HostCommissionSpec",
            )
            self.check(
                sorted(name for name in names if name in SPEC_REQUIRED) == SPEC_REQUIRED,
                f"{label} element {index} must carry every required member",
            )
            self.check(
                names == [name for name in SPEC_MEMBER_ORDER if name in names],
                f"{label} element {index} must follow the specification member order, got {names}",
            )
            for name, value in element:
                self.check(
                    isinstance(value, str) and value.strip() != "",
                    f"{label} element {index} sends {name} as {value!r}; unset optional "
                    "members must be omitted, never sent as null or an empty string",
                )
        return payload

    def check_optionals_omitted(self, entry: dict[str, Any], omitted: list[str]) -> None:
        label = entry["operationId"]
        for index, element in enumerate(
            json.loads(entry["body"], object_pairs_hook=lambda pairs: pairs)
        ):
            names = [name for name, _ in element]
            for member in omitted:
                self.check(
                    member not in names,
                    f"{label} element {index} sends unset optional member {member}",
                )

    def check_no_mutation(self) -> None:
        self.check(
            not self.of("commissionHosts"),
            "a refused precheck must never reach POST /v1/hosts",
        )
        self.check(
            self.commission_effect_total() == 0,
            "a refused precheck must leave no host commissioned",
        )
        self.check(
            self.commissioned_fqdns() == [],
            "a refused precheck must leave no host commissioned",
        )

    def check_off_contract(self) -> None:
        self.check(
            all(entry["operationId"] is not None for entry in self.entries),
            "the client reached a path outside the focused contract",
        )
        self.check(
            all(entry["responseStatus"] != 401 for entry in self.entries),
            "the client failed to authenticate against the contracted service",
        )

    def check_secret_free(self, text: str, what: str) -> None:
        self.check(self.password not in text, f"{what} leaks the host password")
        self.check(self.token not in text, f"{what} leaks the bearer token")



# --------------------------------------------------------------------------- #
# cases
# --------------------------------------------------------------------------- #


def case_pass_commissions(module: Any) -> None:
    """A clean precheck gates open, and both bodies carry the identical wire shape."""
    case = Case("pass-commissions", {"pollsBeforeTerminal": 2})
    case.run(module)
    case.check(case.error is None, f"a passing precheck must not raise: {case.error!r}")
    case.check_off_contract()
    case.check(
        case.operation_sequence()
        == [
            "validateHostCommissionSpec",
            "getHostCommissionValidationByID",
            "getHostCommissionValidationByID",
            "commissionHosts",
        ],
        f"unexpected call sequence: {case.operation_sequence()}",
    )

    submit = case.of("validateHostCommissionSpec")[0]
    case.check_transport(submit)
    payload = case.check_spec_body(submit)
    case.check(
        [dict(element) for element in payload]
        == [
            {
                "fqdn": case.fqdn,
                "username": "root",
                "password": case.password,
                "storageType": "VSAN_ESA",
                "networkPoolId": case.network_pool_id,
            }
        ],
        "the precheck body must carry the caller's exact required member values",
    )
    case.check_optionals_omitted(submit, SPEC_OPTIONAL)

    for poll in case.of("getHostCommissionValidationByID"):
        case.check_transport(poll)
        case.check(
            poll["path"] == f"/v1/hosts/validations/{case.validation_id}",
            f"the poll must target the id from the 202, got {poll['path']}",
        )
        case.check(
            poll["pathParams"].get("id") == case.validation_id,
            "the poll must carry the validation id as a path parameter",
        )
        case.check(poll["bodyLength"] == 0, "a validation poll must not carry a body")
        case.check(
            not case.header(poll, "content-type"),
            "a validation poll must not declare a Content-Type",
        )

    commission = case.of("commissionHosts")[0]
    case.check_transport(commission)
    case.check_spec_body(commission)
    case.check_optionals_omitted(commission, SPEC_OPTIONAL)
    case.check(
        json.loads(commission["body"]) == json.loads(submit["body"]),
        "the commissioned specifications must be the ones that were prechecked",
    )

    case.check(
        case.commission_effect_total() == 1 and case.commissioned_fqdns() == [case.fqdn],
        "exactly the prechecked host must be commissioned",
    )
    outcome = case.outcome
    case.check(outcome is not None, "a passing precheck must return an outcome")
    case.check(
        getattr(outcome, "commissioned", None) is True,
        "the outcome must report that hosts were commissioned",
    )
    case.check(
        getattr(outcome, "validation_id", None) == case.validation_id,
        "the outcome must carry the validation id",
    )
    case.check(
        getattr(outcome, "execution_status", None) == "COMPLETED"
        and getattr(outcome, "result_status", None) == "SUCCEEDED",
        "the outcome must carry the terminal precheck statuses",
    )
    case.check(
        getattr(outcome, "task_id", None) == case.task_id,
        "the outcome must carry the commissioning task id",
    )
    case.check(
        getattr(outcome, "poll_count", None) == 2,
        f"the outcome must count only validation polls, got {getattr(outcome, 'poll_count', None)}",
    )
    case.check(
        case.sleeps == [7.5],
        f"the client must wait only between non-terminal polls, slept {case.sleeps}",
    )


def case_optional_members_travel(module: Any) -> None:
    """Supplied optional members are sent, in specification order, alongside the rest."""
    case = Case("optional-members-travel", {})

    def make_specs(mod: Any) -> list[Any]:
        return [
            mod.HostCommissionSpec(
                fqdn=case.fqdn,
                username="root",
                password=case.password,
                storage_type="VVOL",
                network_pool_id=case.network_pool_id,
                vvol_storage_protocol_type="FC",
                ssl_thumbprint="AA:BB:CC",
            ),
            mod.HostCommissionSpec(
                fqdn=f"second-{case.fqdn}",
                username="root",
                password=case.password,
                storage_type="VSAN_ESA",
                network_pool_id=case.network_pool_id,
                network_pool_name="engineering-pool",
                ssh_thumbprint="11:22:33",
            ),
        ]

    case.run(module, make_specs)
    case.check(case.error is None, f"a passing precheck must not raise: {case.error!r}")
    case.check_off_contract()

    submit = case.of("validateHostCommissionSpec")[0]
    payload = case.check_spec_body(submit)
    case.check(len(payload) == 2, "both specifications must travel in one array")
    first = [name for name, _ in payload[0]]
    second = [name for name, _ in payload[1]]
    case.check(
        first
        == [
            "fqdn",
            "username",
            "password",
            "storageType",
            "vvolStorageProtocolType",
            "networkPoolId",
            "sslThumbprint",
        ],
        f"the first element carries the wrong members: {first}",
    )
    case.check(
        second
        == [
            "fqdn",
            "username",
            "password",
            "storageType",
            "networkPoolId",
            "networkPoolName",
            "sshThumbprint",
        ],
        f"the second element carries the wrong members: {second}",
    )
    case.check(
        [dict(element) for element in payload]
        == [
            {
                "fqdn": case.fqdn,
                "username": "root",
                "password": case.password,
                "storageType": "VVOL",
                "vvolStorageProtocolType": "FC",
                "networkPoolId": case.network_pool_id,
                "sslThumbprint": "AA:BB:CC",
            },
            {
                "fqdn": f"second-{case.fqdn}",
                "username": "root",
                "password": case.password,
                "storageType": "VSAN_ESA",
                "networkPoolId": case.network_pool_id,
                "networkPoolName": "engineering-pool",
                "sshThumbprint": "11:22:33",
            },
        ],
        "the precheck body must preserve every supplied member value",
    )
    case.check(
        "sshThumbprint" not in first,
        "the first element must omit its unset SSH thumbprint",
    )
    commission = case.of("commissionHosts")[0]
    case.check(
        json.loads(commission["body"]) == json.loads(submit["body"]),
        "the commissioned specifications must be the ones that were prechecked",
    )
    case.check(case.commission_effect_total() == 1, "one commissioning call must be made")


def case_failed_result_blocks(module: Any) -> None:
    """A terminal FAILED result must stop before the mutating call."""
    checks = [
        failed_check("Host esx is already part of a domain"),
        {
            "description": "Host DNS resolves correctly",
            "severity": "INFO",
            "resultStatus": "SUCCEEDED",
        },
        failed_check("Host certificate is not trusted"),
    ]
    checks[2]["resultStatus"] = "  failed  "
    case = Case(
        "failed-result-blocks",
        {"resultStatus": "FAILED", "validationChecks": checks, "pollsBeforeTerminal": 2},
    )
    case.run(module)
    case.check(
        isinstance(case.error, module.PrecheckFailedError),
        f"a failed precheck must raise PrecheckFailedError, got {case.error!r}",
    )
    case.check_off_contract()
    case.check(
        case.operation_sequence()
        == [
            "validateHostCommissionSpec",
            "getHostCommissionValidationByID",
            "getHostCommissionValidationByID",
        ],
        f"unexpected call sequence: {case.operation_sequence()}",
    )
    case.check_no_mutation()
    error = case.error
    case.check(
        getattr(error, "validation_id", None) == case.validation_id,
        "PrecheckFailedError must carry the validation id",
    )
    case.check(
        getattr(error, "execution_status", None) == "COMPLETED"
        and getattr(error, "result_status", None) == "FAILED",
        "PrecheckFailedError must carry the terminal statuses",
    )
    reported = getattr(error, "failed_checks", None)
    case.check(
        isinstance(reported, list)
        and [item.get("description") for item in reported]
        == [checks[0]["description"], checks[2]["description"]],
        "PrecheckFailedError must carry every failed check and no passing check",
    )
    case.check_secret_free(str(error), "the PrecheckFailedError message")
    case.check_secret_free(repr(error), "the PrecheckFailedError repr")


def case_warning_blocks(module: Any) -> None:
    """WARNING is a terminal result the specification names, and it is not a pass."""
    case = Case(
        "warning-blocks",
        {
            "resultStatus": "WARNING",
            "validationChecks": [
                {
                    "description": "Host firmware is below the recommended level",
                    "severity": "WARNING",
                    "resultStatus": "SUCCEEDED",
                    "acknowledge": True,
                }
            ],
        },
    )
    case.run(module)
    case.check(
        isinstance(case.error, module.PrecheckFailedError),
        f"a WARNING precheck must raise PrecheckFailedError, got {case.error!r}",
    )
    case.check_off_contract()
    case.check_no_mutation()
    case.check(
        getattr(case.error, "result_status", None) == "WARNING",
        "PrecheckFailedError must carry the WARNING result",
    )


def case_execution_failed_blocks(module: Any) -> None:
    """A precheck that never completed must block even if its result reads clean.

    resultStatus only means anything once executionStatus reached COMPLETED, so a
    stale SUCCEEDED alongside a FAILED execution must not open the gate.
    """
    case = Case(
        "execution-failed-blocks",
        {"executionStatus": "FAILED", "resultStatus": "SUCCEEDED"},
    )
    case.run(module)
    case.check(
        isinstance(case.error, module.PrecheckFailedError),
        f"a failed execution must raise PrecheckFailedError, got {case.error!r}",
    )
    case.check_off_contract()
    case.check_no_mutation()
    case.check(
        getattr(case.error, "execution_status", None) == "FAILED",
        "PrecheckFailedError must carry the FAILED execution status",
    )


def case_skipped_without_result_blocks(module: Any) -> None:
    """A terminal precheck that reported no result at all must block."""
    case = Case(
        "skipped-without-result-blocks",
        {"executionStatus": "SKIPPED", "resultStatus": None, "pollsBeforeTerminal": 3},
    )
    case.run(module)
    case.check(
        isinstance(case.error, module.PrecheckFailedError),
        f"a skipped precheck must raise PrecheckFailedError, got {case.error!r}",
    )
    case.check_off_contract()
    case.check_no_mutation()
    case.check(
        len(case.of("getHostCommissionValidationByID")) == 3,
        "the client must keep polling until the precheck stops running",
    )
    case.check(
        getattr(case.error, "execution_status", None) == "SKIPPED",
        "PrecheckFailedError must carry the SKIPPED execution status",
    )
    case.check(
        getattr(case.error, "result_status", None) is None,
        "an absent resultStatus must be reported as None, not invented",
    )


def case_cancelled_blocks(module: Any) -> None:
    """CANCELLED is terminal and is not a pass, whatever the result status says."""
    case = Case(
        "cancelled-blocks",
        {"executionStatus": "CANCELLED", "resultStatus": "SUCCEEDED"},
    )
    case.run(module)
    case.check(
        isinstance(case.error, module.PrecheckFailedError),
        f"a cancelled precheck must raise PrecheckFailedError, got {case.error!r}",
    )
    case.check_off_contract()
    case.check_no_mutation()


def case_unknown_blocks(module: Any) -> None:
    """UNKNOWN is terminal and must shut the gate without another poll or wait."""
    case = Case(
        "unknown-blocks",
        {"executionStatus": "UNKNOWN", "resultStatus": "UNKNOWN"},
    )
    case.run(module)
    case.check(
        isinstance(case.error, module.PrecheckFailedError),
        f"an UNKNOWN execution must raise PrecheckFailedError, got {case.error!r}",
    )
    case.check_off_contract()
    case.check_no_mutation()
    case.check(
        len(case.of("getHostCommissionValidationByID")) == 1 and case.sleeps == [],
        "UNKNOWN must stop polling immediately without a wait",
    )


def case_statuses_are_normalized(module: Any) -> None:
    """Whitespace and case in polled statuses must not keep a clean pass shut."""
    case = Case(
        "statuses-are-normalized",
        {"executionStatus": "  completed  ", "resultStatus": "  succeeded  "},
    )
    case.run(module)
    case.check(case.error is None, f"normalized passing statuses raised: {case.error!r}")
    case.check_off_contract()
    case.check(
        case.commission_effect_total() == 1,
        "normalized COMPLETED/SUCCEEDED statuses must open the gate",
    )
    case.check(
        getattr(case.outcome, "execution_status", None) == "COMPLETED"
        and getattr(case.outcome, "result_status", None) == "SUCCEEDED",
        "the outcome must carry normalized statuses",
    )


def case_empty_validation_id_stops(module: Any) -> None:
    """The submission placeholder must name a non-empty validation to poll."""
    case = Case("empty-validation-id-stops", {"acceptedValidationId": "  "})
    case.run(module)
    case.check(
        isinstance(case.error, module.SddcManagerError),
        f"an empty validation id must raise SddcManagerError, got {case.error!r}",
    )
    case.check_off_contract()
    case.check(
        case.operation_sequence() == ["validateHostCommissionSpec"],
        f"an empty id must stop before polling or commissioning: {case.operation_sequence()}",
    )
    case.check_no_mutation()


def case_cancellation_in_progress_times_out(module: Any) -> None:
    """A named status not in the terminal list remains bounded and non-terminal."""
    case = Case(
        "cancellation-in-progress-times-out",
        {
            "executionStatus": "  cancellation in progress  ",
            "resultStatus": "CANCELLATION_IN_PROGRESS",
        },
    )
    case.run(module)
    case.check(
        isinstance(case.error, module.PrecheckTimeoutError),
        "CANCELLATION_IN_PROGRESS must remain non-terminal until max_polls",
    )
    case.check_off_contract()
    case.check_no_mutation()
    case.check(
        getattr(case.error, "execution_status", None) == "CANCELLATION_IN_PROGRESS"
        and getattr(case.error, "poll_count", None) == 5,
        "the timeout must carry the normalized last status and poll count",
    )
    case.check(
        case.sleeps == [7.5, 7.5, 7.5, 7.5],
        f"the client must wait only between permitted polls, slept {case.sleeps}",
    )


def case_unclassified_status_times_out(module: Any) -> None:
    """A status the contract does not name stays non-terminal and stays bounded."""
    case = Case(
        "unclassified-status-times-out",
        {"executionStatus": "REALLY_ALMOST_DONE", "pollsBeforeTerminal": 1},
    )
    case.run(module)
    case.check(
        isinstance(case.error, module.PrecheckTimeoutError),
        f"an unclassified status must raise PrecheckTimeoutError, got {case.error!r}",
    )
    case.check_off_contract()
    case.check_no_mutation()
    case.check(
        len(case.of("getHostCommissionValidationByID")) == 5,
        "the client must poll exactly max_polls times before giving up",
    )
    case.check(
        getattr(case.error, "poll_count", None) == 5,
        "PrecheckTimeoutError must report how many polls were spent",
    )
    case.check(
        getattr(case.error, "validation_id", None) == case.validation_id
        and getattr(case.error, "execution_status", None) == "REALLY_ALMOST_DONE",
        "PrecheckTimeoutError must carry the validation id and last execution status",
    )
    case.check(
        case.sleeps == [7.5, 7.5, 7.5, 7.5],
        f"the client must not wait after the final permitted poll, slept {case.sleeps}",
    )


def case_never_terminal_times_out(module: Any) -> None:
    """A precheck stuck IN_PROGRESS must time out rather than commission."""
    case = Case("never-terminal-times-out", {"pollsBeforeTerminal": 99})
    case.run(module)
    case.check(
        isinstance(case.error, module.PrecheckTimeoutError),
        f"a stuck precheck must raise PrecheckTimeoutError, got {case.error!r}",
    )
    case.check_off_contract()
    case.check_no_mutation()
    case.check(
        len(case.of("getHostCommissionValidationByID")) == 5,
        "the client must stop at max_polls",
    )
    case.check_secret_free(str(case.error), "the PrecheckTimeoutError message")


def case_spec_repr_redacts(module: Any) -> None:
    """A specification must not spill the host password when it is printed."""
    password = secrets.token_urlsafe(18)
    spec = module.HostCommissionSpec(
        fqdn="esx-repr.lab.local",
        username="root",
        password=password,
        storage_type="VSAN_ESA",
        network_pool_id="pool-repr",
    )
    require(
        password not in repr(spec),
        "repr(HostCommissionSpec) must redact the host password",
    )
    require(
        password not in str(spec),
        "str(HostCommissionSpec) must redact the host password",
    )
    wire = spec.to_wire()
    require(
        list(wire) == ["fqdn", "username", "password", "storageType", "networkPoolId"],
        f"to_wire must emit specification-ordered required members, got {list(wire)}",
    )
    require(
        wire["password"] == password,
        "to_wire must carry the real password to the service",
    )


def case_service_error_redacts_secrets(module: Any) -> None:
    """Even a hostile service error must not echo request credentials to callers."""
    case = Case("service-error-redacts-secrets", {})
    case.scenario["commissionErrorMessage"] = (
        f"rejected bearer {case.token} and host password {case.password}"
    )
    case.run(module)
    case.check(
        isinstance(case.error, module.SddcManagerError),
        f"a service rejection must raise SddcManagerError, got {case.error!r}",
    )
    case.check_off_contract()
    case.check(
        case.operation_sequence()
        == [
            "validateHostCommissionSpec",
            "getHostCommissionValidationByID",
            "commissionHosts",
        ],
        f"unexpected service-error call sequence: {case.operation_sequence()}",
    )
    case.check(
        case.of("commissionHosts")[0]["responseStatus"] == 400,
        "the loopback service must genuinely reject the commissioning request",
    )
    case.check(
        case.commission_effect_total() == 0 and case.commissioned_fqdns() == [],
        "the rejected commissioning request must have no service-side effect",
    )
    case.check_secret_free(str(case.error), "the service-error message")
    case.check_secret_free(repr(case.error), "the service-error repr")


CASES = (
    case_pass_commissions,
    case_optional_members_travel,
    case_failed_result_blocks,
    case_warning_blocks,
    case_execution_failed_blocks,
    case_skipped_without_result_blocks,
    case_cancelled_blocks,
    case_unknown_blocks,
    case_statuses_are_normalized,
    case_empty_validation_id_stops,
    case_cancellation_in_progress_times_out,
    case_unclassified_status_times_out,
    case_never_terminal_times_out,
    case_spec_repr_redacts,
    case_service_error_redacts_secrets,
)


def main() -> int:
    try:
        verify_contract()
        verify_package_shape()
        if str(SRC) not in sys.path:
            sys.path.insert(0, str(SRC))
        try:
            module = importlib.import_module(PACKAGE)
        except Exception as error:  # noqa: BLE001
            raise VerificationError(
                f"package import failed: {type(error).__name__}: {error}"
            ) from None
        for name in (
            "HostCommissionSpec",
            "CommissionOutcome",
            "SddcManagerClient",
            "SddcManagerError",
            "PrecheckFailedError",
            "PrecheckTimeoutError",
        ):
            require(hasattr(module, name), f"the package must export {name}")
        require(
            issubclass(module.SddcManagerError, Exception),
            "SddcManagerError must be an exception type",
        )
        for name in ("PrecheckFailedError", "PrecheckTimeoutError"):
            require(
                issubclass(getattr(module, name), module.SddcManagerError),
                f"{name} must derive from SddcManagerError",
            )
        verify_public_signatures(module)
        for case in CASES:
            case(module)
    except VerificationError as error:
        print(f"VERIFICATION FAILED: {error}", file=sys.stderr)
        return 1
    print("VERIFICATION PASSED: the host commission precheck gates every mutating call")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
