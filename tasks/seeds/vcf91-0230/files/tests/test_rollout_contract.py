"""Contract test for the SDDC LCM staged rollout.

Nothing here talks to a VMware endpoint. The test starts the loopback mock in
``tools/mock_sddc_lcm.py``, runs the rollout against it, and then asserts three
things:

1. ``docs/contract.json`` and ``docs/official_sources.json`` agree with the
   published SDDC LCM OpenAPI specification.
2. The exact wire shape of every request the rollout sent, including that an
   optional field with no value is omitted rather than sent empty.
3. The report describes the multi-step rollout truthfully: what succeeded
   before the failure, what failed, and what was never attempted.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = ROOT / "fixtures" / "rollout-plan.json"
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"

TOKEN = "eyJhbGciOiJIUzI1NiJ9.mock-sddc-lcm-token"
CORRELATION_ID = "39ab89c8-a945-4290-9327-13c5bd3f595c"
DEPOT_FQDN = "depot.vcf.example.com"

CID_OPS = "c0a80101-0000-4000-8000-00000000000a"
CID_AUT = "c0a80101-0000-4000-8000-00000000000b"
CID_IDB = "c0a80101-0000-4000-8000-00000000000c"

T_DEPOT = "1f0a5c2d-0000-4000-8000-000000000001"
T_OPS_PRE = "1f0a5c2d-0000-4000-8000-000000000002"
T_OPS_APPLY = "1f0a5c2d-0000-4000-8000-000000000003"
T_AUT_PRE = "1f0a5c2d-0000-4000-8000-000000000004"
T_AUT_APPLY = "1f0a5c2d-0000-4000-8000-000000000005"

V_OPS = "9.1.0.0.24010001"
V_AUT = "9.1.0.0.24010002"
V_IDB = "9.1.0.0.24010003"


def binary_url(component, version):
    return "https://%s/depot/PROD/COMP/%s/%s/upgrade-manifest" % (
        DEPOT_FQDN,
        component,
        version,
    )


# --------------------------------------------------------------------------
# What the specification says.  Derived from
# specifications/sddc-lcm/sddc-lcm-openapi.yaml in vmware/vcf-api-specs.
# --------------------------------------------------------------------------

EXPECTED_SOURCE = {
    "repository": "vmware/vcf-api-specs",
    "specPath": "specifications/sddc-lcm/sddc-lcm-openapi.yaml",
    "license": "Apache-2.0",
    "openapi": "3.0.4",
    "apiTitle": "VCF SDDC LCM Service APIs",
    "apiVersion": "9.1.0.0",
}

EXPECTED_SERVER = "https://vcf.broadcom.com/sddc-lcm"

EXPECTED_SECURITY = {
    "scheme": "bearerToken",
    "type": "http",
    "httpScheme": "Bearer",
    "bearerFormat": "JWT",
}

EXPECTED_OPERATIONS = {
    "setDepot": {
        "method": "POST",
        "path": "/v1/depot",
        "successStatus": 202,
        "requestSchema": "FleetDepotSpec",
        "responseSchema": "Task",
        "pathParams": [],
        "queryParams": {"required": [], "optional": []},
        "optionalHeaders": ["X-Correlation-Id"],
    },
    "resolveDepotComponents": {
        "method": "POST",
        "path": "/v1/depot/components",
        "successStatus": 200,
        "requestSchema": "DepotComponentsSpec",
        "responseSchema": "ResolvedComponentVersions",
        "pathParams": [],
        "queryParams": {"required": [], "optional": []},
        "optionalHeaders": [],
    },
    "getComponents": {
        "method": "GET",
        "path": "/v1/components",
        "successStatus": 200,
        "requestSchema": None,
        "responseSchema": "Components",
        "pathParams": [],
        "queryParams": {"required": [], "optional": ["scope"]},
        "optionalHeaders": [],
    },
    "performComponentAction": {
        "method": "POST",
        "path": "/v1/components/{componentId}",
        "successStatus": 202,
        "requestSchema": "ComponentUpgradeSpec",
        "responseSchema": "Task",
        "pathParams": ["componentId"],
        "queryParams": {"required": ["action"], "optional": []},
        "optionalHeaders": ["X-Correlation-Id"],
        "actionEnum": ["apply", "precheck", "refresh", "restart", "shutdown", "start"],
    },
    "getTask": {
        "method": "GET",
        "path": "/v1/tasks/{taskId}",
        "successStatus": 200,
        "requestSchema": None,
        "responseSchema": "Task",
        "pathParams": ["taskId"],
        "queryParams": {"required": [], "optional": []},
        "optionalHeaders": [],
    },
}

EXPECTED_SCHEMAS = {
    "ComponentDesiredSpec": {
        "required": ["depot", "software"],
        "optional": ["additionalInput", "policy", "userInput"],
    },
    "ComponentUpgradeSpec": {
        "required": ["componentSpec"],
        "optional": ["correlationId", "lcmPlatformSpec"],
    },
    "ComponentVersionSpec": {"required": ["component"], "optional": ["version"]},
    "DepotComponentsSpec": {
        "required": ["componentVersions", "fleetDepotSpec"],
        "optional": ["version"],
    },
    "DepotSpec": {"required": ["url"], "optional": ["certificate"]},
    "FleetDepotSpec": {"required": ["certificate", "fqdn"], "optional": []},
    "LcmPlatformSpec": {"required": ["performBackup"], "optional": []},
    "SoftwareSpec": {"required": ["version"], "optional": []},
}

PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_SPEC_SHA256 = (
    "158cab89bc56e1bb80b662a859499efc6ee57c1d35503d4d1f855809c213436c"
)


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class Rollout:
    """One end-to-end run of the rollout against the loopback mock."""

    def __init__(self, contract_path=CONTRACT_PATH, plan_path=PLAN_PATH, mock_args=None):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="sddc-lcm-contract-")
        self.log_path = os.path.join(self.tmpdir.name, "requests.jsonl")
        self.report_path = os.path.join(self.tmpdir.name, "report.json")
        self.contract_path = Path(contract_path)
        self.plan_path = Path(plan_path)
        self.mock_args = list(mock_args or ())
        self.mock = None
        self.result = None
        self.requests = []
        self.report = None

    def start_mock(self):
        command = [
                sys.executable,
                "-u",
                str(ROOT / "tools" / "mock_sddc_lcm.py"),
                "--contract",
                str(self.contract_path),
                "--log",
                self.log_path,
                "--host",
                "127.0.0.1",
                "--port",
                "0",
            ]
        command.extend(self.mock_args)
        self.mock = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        holder = {}

        def read_line():
            holder["line"] = self.mock.stdout.readline()

        reader = threading.Thread(target=read_line, daemon=True)
        reader.start()
        reader.join(30)
        line = holder.get("line", "")
        if not line.startswith("LISTENING "):
            stderr = ""
            try:
                self.mock.kill()
                stderr = self.mock.stderr.read()
            except Exception:
                pass
            raise AssertionError(
                "the loopback mock did not start; it builds its routes from "
                "docs/contract.json.\nstderr:\n%s" % stderr
            )
        return int(line.split()[1])

    def run(self):
        port = self.start_mock()
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONPATH", None)
        self.result = subprocess.run(
            [
                sys.executable,
                "-m",
                "vcf_lcm.rollout",
                "--plan",
                str(self.plan_path),
                "--base-url",
                "http://127.0.0.1:%d" % port,
                "--token",
                TOKEN,
                "--report",
                self.report_path,
                "--contract",
                str(self.contract_path),
                "--poll-interval",
                "0",
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=240,
        )
        if os.path.exists(self.log_path):
            with open(self.log_path, encoding="utf-8") as handle:
                self.requests = [json.loads(l) for l in handle if l.strip()]
        if os.path.exists(self.report_path):
            try:
                self.report = load_json(self.report_path)
            except ValueError as exc:
                raise AssertionError("--report did not receive valid JSON: %s" % exc)

    def stop(self):
        if self.mock is not None:
            self.mock.terminate()
            try:
                self.mock.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.mock.kill()
        self.tmpdir.cleanup()


class ContractTest(unittest.TestCase):
    rollout = None
    setup_error = None

    @classmethod
    def setUpClass(cls):
        cls.plan = load_json(PLAN_PATH)
        cls.rollout = Rollout()
        try:
            cls.rollout.run()
        except Exception as exc:  # surfaced by _require_run
            cls.setup_error = exc

    @classmethod
    def tearDownClass(cls):
        if cls.rollout is not None:
            cls.rollout.stop()

    # -- helpers ---------------------------------------------------------

    def _require_run(self):
        if self.setup_error is not None:
            self.fail("the rollout could not be executed: %s" % self.setup_error)
        result = self.rollout.result
        self.assertIsNotNone(result, "the rollout was never executed")
        return result

    def _requests(self):
        self._require_run()
        self.assertTrue(
            self.rollout.requests,
            "the mock recorded no requests; the rollout sent nothing.\n"
            "stdout:\n%s\nstderr:\n%s"
            % (self.rollout.result.stdout, self.rollout.result.stderr),
        )
        return self.rollout.requests

    def _by_operation(self, operation_id):
        return [r for r in self._requests() if r["operationId"] == operation_id]

    def _one(self, operation_id):
        matches = self._by_operation(operation_id)
        self.assertEqual(
            1,
            len(matches),
            "expected exactly one %s request, saw %d" % (operation_id, len(matches)),
        )
        return matches[0]

    def _report(self):
        self._require_run()
        self.assertIsNotNone(
            self.rollout.report,
            "no report was written to --report.\nstdout:\n%s\nstderr:\n%s"
            % (self.rollout.result.stdout, self.rollout.result.stderr),
        )
        return self.rollout.report

    def _action_request(self, component_id, action):
        matches = [
            r
            for r in self._by_operation("performComponentAction")
            if r["pathParams"].get("componentId") == component_id
            and r["queryParams"].get("action") == [action]
        ]
        self.assertEqual(
            1,
            len(matches),
            "expected exactly one %s action on component %s, saw %d"
            % (action, component_id, len(matches)),
        )
        return matches[0]

    def _document(self, path, label):
        if not path.is_file():
            self.fail("%s is missing" % label)
        try:
            return load_json(path)
        except ValueError as exc:
            self.fail("%s is not valid JSON: %s" % (label, exc))

    def _contract(self):
        return self._document(CONTRACT_PATH, "docs/contract.json")

    def _sources(self):
        return self._document(SOURCES_PATH, "docs/official_sources.json")

    # -- 1. the contract -------------------------------------------------

    def test_contract_file_is_present_and_well_formed(self):
        self.assertTrue(
            CONTRACT_PATH.is_file(), "docs/contract.json is missing"
        )
        contract = self._contract()
        for key in ("source", "server", "security", "operations", "schemas"):
            self.assertIn(key, contract, "docs/contract.json has no %r block" % key)

    def test_contract_source_names_the_specification(self):
        source = self._contract()["source"]
        commit = source.get("commit")
        self.assertIsInstance(commit, str, "source.commit must be a string")
        self.assertRegex(
            commit,
            r"^[0-9a-f]{40}$",
            "source.commit must be the full 40 character commit sha the spec was "
            "read at",
        )
        self.assertEqual(
            PINNED_COMMIT,
            commit,
            "source.commit must be the immutable specification commit named in "
            "the task",
        )
        self.assertEqual(
            EXPECTED_SOURCE,
            {k: v for k, v in source.items() if k != "commit"},
            "source must identify the specification exactly",
        )

    def test_contract_server_and_security(self):
        contract = self._contract()
        self.assertEqual(EXPECTED_SERVER, contract["server"])
        self.assertEqual(EXPECTED_SECURITY, contract["security"])

    def test_contract_names_exactly_the_operations_used(self):
        operations = self._contract()["operations"]
        self.assertEqual(
            sorted(EXPECTED_OPERATIONS),
            sorted(operations),
            "the contract must name exactly the operationIds the rollout uses",
        )

    def test_contract_operations_match_the_specification(self):
        operations = self._contract()["operations"]
        for op_id, expected in EXPECTED_OPERATIONS.items():
            with self.subTest(operationId=op_id):
                self.assertIn(op_id, operations)
                actual = operations[op_id]
                self.assertEqual(
                    expected,
                    actual,
                    "operation %s does not match the specification" % op_id,
                )

    def test_contract_request_schemas_match_the_specification(self):
        schemas = self._contract()["schemas"]
        self.assertEqual(
            sorted(EXPECTED_SCHEMAS),
            sorted(schemas),
            "the contract must describe exactly the request schemas the rollout "
            "builds",
        )
        for name, expected in EXPECTED_SCHEMAS.items():
            with self.subTest(schema=name):
                self.assertEqual(
                    expected,
                    schemas[name],
                    "schema %s does not match the specification" % name,
                )

    def test_official_sources_record_the_provenance(self):
        self.assertTrue(
            SOURCES_PATH.is_file(), "docs/official_sources.json is missing"
        )
        data = self._sources()
        self.assertIn("sources", data)
        matching = [
            s
            for s in data["sources"]
            if s.get("path") == EXPECTED_SOURCE["specPath"]
        ]
        self.assertEqual(
            1,
            len(matching),
            "expected exactly one source entry for %s" % EXPECTED_SOURCE["specPath"],
        )
        entry = matching[0]
        self.assertEqual(
            {
                "title",
                "repository",
                "path",
                "commit",
                "url",
                "rawUrl",
                "license",
                "retrieved",
                "sha256",
                "operationIds",
            },
            set(entry),
            "the provenance entry must use exactly the documented fields",
        )
        self.assertEqual(
            "VCF SDDC LCM Service APIs 9.1.0.0 OpenAPI specification",
            entry.get("title"),
        )
        self.assertEqual("https://github.com/vmware/vcf-api-specs", entry.get("repository"))
        self.assertEqual("Apache-2.0", entry.get("license"))
        commit = entry.get("commit")
        self.assertRegex(str(commit), r"^[0-9a-f]{40}$", "commit sha is required")
        self.assertEqual(PINNED_COMMIT, commit)
        self.assertEqual(
            self._contract()["source"]["commit"],
            commit,
            "the contract and the provenance record must cite the same commit",
        )
        self.assertRegex(
            str(entry.get("retrieved", "")),
            r"^\d{4}-\d{2}-\d{2}$",
            "retrieved must be an ISO 8601 date",
        )
        self.assertEqual(
            sorted(EXPECTED_OPERATIONS),
            sorted(entry.get("operationIds") or []),
            "the provenance record must list every operationId taken from the spec",
        )
        digest = str(entry.get("sha256", ""))
        self.assertRegex(
            digest,
            r"^[0-9a-f]{64}$",
            "sha256 of the specification file at that commit is required",
        )
        self.assertEqual(
            "https://github.com/vmware/vcf-api-specs/blob/%s/%s"
            % (PINNED_COMMIT, EXPECTED_SOURCE["specPath"]),
            entry.get("url"),
        )
        self.assertEqual(
            "https://raw.githubusercontent.com/vmware/vcf-api-specs/%s/%s"
            % (PINNED_COMMIT, EXPECTED_SOURCE["specPath"]),
            entry.get("rawUrl"),
        )
        self.assertEqual(
            PINNED_SPEC_SHA256,
            digest,
            "the sha256 does not match the specification file at commit %s"
            % PINNED_COMMIT,
        )

    # -- 2. the package --------------------------------------------------

    def test_package_uses_only_the_standard_library(self):
        package = ROOT / "vcf_lcm"
        self.assertTrue(package.is_dir(), "the vcf_lcm package is missing")
        modules = sorted(package.rglob("*.py"))
        self.assertTrue(modules, "the vcf_lcm package contains no modules")
        allowed = set(sys.stdlib_module_names) | {"vcf_lcm"}
        for module in modules:
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import inside the package
                        continue
                    names = [(node.module or "").split(".")[0]]
                else:
                    continue
                for name in names:
                    with self.subTest(module=module.name, imported=name):
                        self.assertIn(
                            name,
                            allowed,
                            "%s imports %r, which is not in the standard library"
                            % (module.relative_to(ROOT), name),
                        )

    def test_cli_reports_failure_through_its_exit_status(self):
        result = self._require_run()
        self.assertEqual(
            1,
            result.returncode,
            "a rollout in which a component failed must exit 1.\n"
            "stdout:\n%s\nstderr:\n%s" % (result.stdout, result.stderr),
        )
        self.assertIn(
            "rollout failed",
            result.stdout.lower(),
            "the CLI must print a short human-readable outcome summary",
        )

    def test_client_behavior_is_driven_by_the_runtime_contract(self):
        """A second contract changes every wire control the prompt names.

        The service still dispatches by operationId.  A client that hard-codes
        the published path, method, status, header placement, or field split
        cannot complete this run.
        """
        contract = copy.deepcopy(self._contract())
        methods = {
            "setDepot": "PUT",
            "resolveDepotComponents": "PATCH",
            "getComponents": "POST",
            "performComponentAction": "PUT",
            "getTask": "POST",
        }
        header_ops = {
            "resolveDepotComponents",
            "getComponents",
            "getTask",
        }
        for operation_id, operation in contract["operations"].items():
            operation["method"] = methods[operation_id]
            operation["path"] = "/contract-probe" + operation["path"]
            operation["successStatus"] = 299
            operation["optionalHeaders"] = (
                ["X-Correlation-Id"] if operation_id in header_ops else []
            )

        depot_schema = contract["schemas"]["DepotComponentsSpec"]
        depot_schema["required"] = sorted(depot_schema["required"] + ["version"])
        depot_schema["optional"] = []
        desired_schema = contract["schemas"]["ComponentDesiredSpec"]
        desired_schema["required"] = sorted(desired_schema["required"] + ["policy"])
        desired_schema["optional"] = [
            name for name in desired_schema["optional"] if name != "policy"
        ]

        with tempfile.TemporaryDirectory(prefix="sddc-lcm-contract-variant-") as tmp:
            contract_path = Path(tmp) / "contract.json"
            with open(contract_path, "w", encoding="utf-8") as handle:
                json.dump(contract, handle)
            rollout = Rollout(contract_path=contract_path)
            try:
                rollout.run()
                self.assertEqual(
                    1,
                    rollout.result.returncode,
                    "the contract-variant rollout should reach the scripted "
                    "component failure:\nstdout:\n%s\nstderr:\n%s"
                    % (rollout.result.stdout, rollout.result.stderr),
                )
                self.assertIsNotNone(rollout.report)
                self.assertEqual("failed", rollout.report.get("outcome"))
                self.assertTrue(rollout.requests)
                for request in rollout.requests:
                    operation_id = request["operationId"]
                    with self.subTest(operationId=operation_id, seq=request["seq"]):
                        self.assertIn(operation_id, methods)
                        self.assertEqual(methods[operation_id], request["method"])
                        self.assertTrue(request["path"].startswith("/contract-probe/"))
                        self.assertEqual(299, request["status"])
                        correlation = request["headers"].get("x-correlation-id")
                        if operation_id in header_ops:
                            self.assertEqual(CORRELATION_ID, correlation)
                        else:
                            self.assertIsNone(correlation)

                resolve = next(
                    request
                    for request in rollout.requests
                    if request["operationId"] == "resolveDepotComponents"
                )
                self.assertIn("version", resolve["body"])
                self.assertIsNone(resolve["body"]["version"])
                automation = next(
                    request
                    for request in rollout.requests
                    if request["operationId"] == "performComponentAction"
                    and request["pathParams"].get("componentId") == CID_AUT
                )
                self.assertIn("policy", automation["body"]["componentSpec"])
                self.assertIsNone(automation["body"]["componentSpec"]["policy"])
            finally:
                rollout.stop()

    def test_depot_failure_reports_every_planned_component_as_skipped(self):
        rollout = Rollout(mock_args=["--fail-depot"])
        try:
            rollout.run()
            self.assertEqual(
                1,
                rollout.result.returncode,
                "a failed depot task is a completed but failed rollout",
            )
            self.assertEqual(
                ["setDepot", "getTask", "getTask"],
                [request["operationId"] for request in rollout.requests],
                "nothing after depot registration may run when its task fails",
            )
            expected_components = [
                {
                    "componentType": entry["componentType"],
                    "componentId": None,
                    "currentVersion": None,
                    "targetVersion": entry["targetVersion"],
                    "outcome": "skipped",
                    "actions": [],
                }
                for entry in self.plan["components"]
            ]
            self.assertEqual(
                {
                    "outcome": "failed",
                    "depot": {
                        "fqdn": DEPOT_FQDN,
                        "taskId": T_DEPOT,
                        "status": "FAILED",
                        "failure": {
                            "taskId": T_DEPOT,
                            "failedStage": "depot-trust",
                            "errors": [
                                {
                                    "id": "com.broadcom.lcm.depot.registration.failed",
                                    "defaultMessage": (
                                        "Fleet depot certificate validation failed"
                                    ),
                                }
                            ],
                        },
                    },
                    "resolvedComponents": [],
                    "components": expected_components,
                },
                rollout.report,
            )
        finally:
            rollout.stop()

    # -- 3. the wire shape ------------------------------------------------

    def test_every_request_was_served_by_a_contract_operation(self):
        for request in self._requests():
            with self.subTest(seq=request["seq"], path=request["path"]):
                self.assertIsNotNone(
                    request["operationId"],
                    "%s %s matched no operation in the contract"
                    % (request["method"], request["path"]),
                )
                self.assertLess(
                    request["status"],
                    400,
                    "%s %s%s was rejected with %d: %s"
                    % (
                        request["method"],
                        request["path"],
                        "?" + request["query"] if request["query"] else "",
                        request["status"],
                        request["bodyRaw"][:400],
                    ),
                )

    def test_request_volume_is_bounded(self):
        requests = self._requests()
        self.assertLessEqual(
            len(requests),
            60,
            "the rollout issued %d requests; polling is not converging"
            % len(requests),
        )

    def test_operation_order(self):
        actual = [
            (
                r["operationId"],
                r["pathParams"].get("componentId"),
                (r["queryParams"].get("action") or [None])[0],
            )
            for r in self._requests()
            if r["operationId"] != "getTask"
        ]
        self.assertEqual(
            [
                ("setDepot", None, None),
                ("resolveDepotComponents", None, None),
                ("getComponents", None, None),
                ("performComponentAction", CID_OPS, "precheck"),
                ("performComponentAction", CID_OPS, "apply"),
                ("performComponentAction", CID_AUT, "precheck"),
                ("performComponentAction", CID_AUT, "apply"),
            ],
            actual,
            "the rollout did not walk the plan in the documented order",
        )

    def test_nothing_was_attempted_for_the_component_after_the_failure(self):
        for request in self._requests():
            with self.subTest(seq=request["seq"]):
                self.assertNotEqual(
                    CID_IDB,
                    request["pathParams"].get("componentId"),
                    "VCF_IDENTITY_BROKER must not be touched after VCF_AUTOMATION "
                    "failed",
                )

    def test_every_request_is_authenticated(self):
        for request in self._requests():
            with self.subTest(seq=request["seq"], op=request["operationId"]):
                self.assertEqual(
                    "Bearer " + TOKEN,
                    request["headers"].get("authorization"),
                    "every call must carry the bearer token",
                )

    def test_bodied_requests_declare_json(self):
        for request in self._requests():
            if request["method"] != "POST":
                continue
            with self.subTest(seq=request["seq"], op=request["operationId"]):
                content_type = request["headers"].get("content-type", "")
                self.assertTrue(
                    content_type.startswith("application/json"),
                    "POST %s sent Content-Type %r"
                    % (request["path"], content_type),
                )

    def test_correlation_id_header_only_where_the_spec_declares_it(self):
        for request in self._requests():
            if request["operationId"] not in EXPECTED_OPERATIONS:
                continue  # reported by test_every_request_was_served_by_...
            header = request["headers"].get("x-correlation-id")
            declared = EXPECTED_OPERATIONS[request["operationId"]]["optionalHeaders"]
            with self.subTest(seq=request["seq"], op=request["operationId"]):
                if "X-Correlation-Id" in declared:
                    self.assertEqual(
                        CORRELATION_ID,
                        header,
                        "%s declares X-Correlation-Id; the plan supplies one"
                        % request["operationId"],
                    )
                else:
                    self.assertIsNone(
                        header,
                        "%s does not declare X-Correlation-Id; it must not be sent"
                        % request["operationId"],
                    )

    def test_set_depot_body(self):
        request = self._one("setDepot")
        self.assertEqual("/v1/depot", request["path"])
        self.assertEqual("", request["query"], "setDepot takes no query parameters")
        self.assertEqual(
            {
                "fqdn": DEPOT_FQDN,
                "certificate": self.plan["depot"]["certificate"],
            },
            request["body"],
            "the FleetDepotSpec body must carry exactly fqdn and certificate",
        )

    def test_resolve_depot_components_body_omits_the_unset_optional(self):
        request = self._one("resolveDepotComponents")
        self.assertEqual("/v1/depot/components", request["path"])
        self.assertEqual("", request["query"])
        body = request["body"]
        self.assertIsInstance(body, dict)
        self.assertNotIn(
            "version",
            body,
            "DepotComponentsSpec.version is optional and the plan does not set it, "
            "so the key must be absent rather than sent empty",
        )
        self.assertEqual(
            {
                "fleetDepotSpec": {
                    "fqdn": DEPOT_FQDN,
                    "certificate": self.plan["depot"]["certificate"],
                },
                "componentVersions": [
                    {"component": "VCF_OPERATIONS", "version": V_OPS},
                    {"component": "VCF_AUTOMATION", "version": V_AUT},
                    {"component": "VCF_IDENTITY_BROKER", "version": V_IDB},
                ],
            },
            body,
        )

    def test_get_components_sends_no_query_string(self):
        request = self._one("getComponents")
        self.assertEqual("/v1/components", request["path"])
        self.assertEqual(
            "",
            request["query"],
            "scope is optional and the plan does not set it, so no query string "
            "may be sent at all",
        )
        self.assertEqual("", request["bodyRaw"], "getComponents takes no body")

    def test_upgrade_body_for_the_component_that_succeeded(self):
        expected = {
            "componentSpec": {
                "software": {"version": V_OPS},
                "depot": {
                    "url": binary_url("VCF_OPERATIONS", V_OPS),
                    "certificate": self.plan["components"][0]["depotCertificates"],
                },
                "policy": self.plan["components"][0]["policy"],
            },
            "lcmPlatformSpec": {"performBackup": True},
            "correlationId": CORRELATION_ID,
        }
        for action in ("precheck", "apply"):
            with self.subTest(action=action):
                request = self._action_request(CID_OPS, action)
                self.assertEqual("/v1/components/" + CID_OPS, request["path"])
                self.assertEqual("action=" + action, request["query"])
                self.assertEqual(expected, request["body"])

    def test_upgrade_body_omits_optional_fields_that_have_no_value(self):
        expected = {
            "componentSpec": {
                "software": {"version": V_AUT},
                "depot": {"url": binary_url("VCF_AUTOMATION", V_AUT)},
            },
            "lcmPlatformSpec": {"performBackup": False},
            "correlationId": CORRELATION_ID,
        }
        for action in ("precheck", "apply"):
            with self.subTest(action=action):
                body = self._action_request(CID_AUT, action)["body"]
                spec = body.get("componentSpec", {})
                self.assertNotIn(
                    "certificate",
                    spec.get("depot", {}),
                    "the plan lists no depot certificates for VCF_AUTOMATION, so "
                    "DepotSpec.certificate must be omitted, not sent as []",
                )
                for optional in ("policy", "userInput", "additionalInput"):
                    self.assertNotIn(
                        optional,
                        spec,
                        "ComponentDesiredSpec.%s is optional and unset, so it must "
                        "be omitted, not sent as {}" % optional,
                    )
                self.assertIn(
                    "lcmPlatformSpec",
                    body,
                    "LcmPlatformSpec.performBackup is required; false is a value, "
                    "not an absence",
                )
                self.assertEqual(expected, body)

    def test_task_polling_shape(self):
        polls = self._by_operation("getTask")
        self.assertTrue(polls, "the rollout never polled a task")
        counts = {}
        for request in polls:
            task_id = request["pathParams"].get("taskId")
            counts[task_id] = counts.get(task_id, 0) + 1
            with self.subTest(seq=request["seq"]):
                self.assertEqual("/v1/tasks/" + str(task_id), request["path"])
                self.assertEqual("", request["query"], "getTask takes no query")
                self.assertEqual("", request["bodyRaw"], "getTask takes no body")
        self.assertEqual(
            {T_DEPOT, T_OPS_PRE, T_OPS_APPLY, T_AUT_PRE, T_AUT_APPLY},
            set(counts),
            "every task the service returned must be polled to a terminal state",
        )
        for task_id, count in counts.items():
            with self.subTest(task=task_id):
                self.assertGreaterEqual(
                    count,
                    2,
                    "task %s reports RUNNING on the first poll; the rollout must "
                    "keep polling until it is terminal" % task_id,
                )

    # -- 4. the report ----------------------------------------------------

    def test_report_describes_the_rollout_truthfully(self):
        expected = {
            "outcome": "failed",
            "depot": {
                "fqdn": DEPOT_FQDN,
                "taskId": T_DEPOT,
                "status": "SUCCEEDED",
            },
            "resolvedComponents": [
                {
                    "component": "VCF_OPERATIONS",
                    "version": V_OPS,
                    "binaryUrl": binary_url("VCF_OPERATIONS", V_OPS),
                },
                {
                    "component": "VCF_AUTOMATION",
                    "version": V_AUT,
                    "binaryUrl": binary_url("VCF_AUTOMATION", V_AUT),
                },
                {
                    "component": "VCF_IDENTITY_BROKER",
                    "version": V_IDB,
                    "binaryUrl": binary_url("VCF_IDENTITY_BROKER", V_IDB),
                },
            ],
            "components": [
                {
                    "componentType": "VCF_OPERATIONS",
                    "componentId": CID_OPS,
                    "currentVersion": "9.0.1.0",
                    "targetVersion": V_OPS,
                    "outcome": "succeeded",
                    "actions": [
                        {
                            "action": "precheck",
                            "taskId": T_OPS_PRE,
                            "status": "SUCCEEDED",
                        },
                        {
                            "action": "apply",
                            "taskId": T_OPS_APPLY,
                            "status": "SUCCEEDED",
                        },
                    ],
                },
                {
                    "componentType": "VCF_AUTOMATION",
                    "componentId": CID_AUT,
                    "currentVersion": "9.0.1.0",
                    "targetVersion": V_AUT,
                    "outcome": "failed",
                    "actions": [
                        {
                            "action": "precheck",
                            "taskId": T_AUT_PRE,
                            "status": "SUCCEEDED",
                        },
                        {
                            "action": "apply",
                            "taskId": T_AUT_APPLY,
                            "status": "FAILED",
                        },
                    ],
                    "failure": {
                        "action": "apply",
                        "taskId": T_AUT_APPLY,
                        "failedStage": "component-apply",
                        "errors": [
                            {
                                "id": "com.broadcom.lcm.ops.component.apply.failed",
                                "defaultMessage": (
                                    "Apply failed for component VCF_AUTOMATION: "
                                    "service vcf-automation-api did not reach "
                                    "RUNNING state within 900 seconds"
                                ),
                            }
                        ],
                    },
                },
                {
                    "componentType": "VCF_IDENTITY_BROKER",
                    "componentId": CID_IDB,
                    "currentVersion": "9.0.1.0",
                    "targetVersion": V_IDB,
                    "outcome": "skipped",
                    "actions": [],
                },
            ],
        }
        self.assertEqual(expected, self._report())


if __name__ == "__main__":
    unittest.main()
