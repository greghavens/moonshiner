#!/usr/bin/env python3
"""Protected verification for the vSAN Data Protection snapshot sweep.

Nothing here contacts a live VMware endpoint: the only server involved is the
loopback mock in mock/, started fresh for each scenario and driven entirely by
the contract that ``docs/contract.json`` declares.

Contract facts taken from the OpenAPI specification are pinned as salted SHA-256
digests, so a mismatch tells you *which* field is wrong without telling you what
the right value is -- read the specification for that.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(ROOT, "docs", "contract.json")
SOURCES_PATH = os.path.join(ROOT, "docs", "official_sources.json")
FIXTURES_PATH = os.path.join(ROOT, "mock", "fixtures", "site.json")
MOCK_PATH = os.path.join(ROOT, "mock", "snapservice_mock.py")
PACKAGE_DIR = os.path.join(ROOT, "vsandp")

CLUSTER = "domain-c1010"
PREFIX = "premigration"
RUN_TIMEOUT = 90

SALT = "vcf91-0245/vsan-data-protection"

# --- pinned specification facts -------------------------------------------- #

SPEC_REPOSITORY = "https://github.com/vmware/vcf-api-specs"
SPEC_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"
SPEC_BLOB_SHA = "bc8d3a70493af8d1ef2f829e86ad21a2020f1c80"

ROLE_LABELS = {
    "session_create": "log in and obtain a session token",
    "session_delete": "log out and invalidate the session token",
    "protection_groups_list": "list the protection groups of a cluster",
    "snapshot_create": "take a manual protection group snapshot (task variant)",
    "task_get": "read the state of one task",
}

OPERATION_FIELDS = (
    "operation_id",
    "method",
    "path",
    "fixed_query",
    "path_params",
    "optional_query_params",
    "success_status",
    "auth",
    "request_body",
    "response_schema",
)

DIGESTS = {
    "*": {
        "scheme:api_key_auth": "8e7b44ea3beeaf4788de4eca08f8a01db2ff8ec1419617c3858b4c40ebc94940",
        "scheme:basic_auth": "7eb3f5522c3f1678e852c3158c34415696cff6319985ed0e9b7642590f0a9295",
        "security_scheme_names": "15602ea02456b77a89bfd8af7c19fd3b219ca0f004bfb564f4e3cf6855ae19bb",
        "server_base_path": "9349c9b71fd68dfbd6feedb66b457a3c3efe38c2b2ee2e5924ac93fb5679bbb1",
    },
    "protection_groups_list": {
        "auth": "c03e0deab0dd7eee11158f503fb0658c9abefdb8866e6a396c0fcddb0c8cc4e0",
        "fixed_query": "2fa04230f4ccbc8c5e2d2c6dc7ee170ad85455780a4f8fc1030c9db269978b9e",
        "method": "f613532c0f88d0afdef8feaca44d922a0ed5524f0f6d4369643080a5317718e6",
        "operation_id": "d638d9e53f2cc73f1a59be7513d1345eca306cd20ef076a487b0f16a7721f3b3",
        "optional_query_params": "f248a667f1872ff561648cbc496226d4de1209e19c3772cf90edf561f2bb3649",
        "path": "4a63533ff421f8084908951593e2734d115d87bf72a52e7cb0ed9ee4a5ad52db",
        "path_params": "5893a14ccdfad5943574ff18eb4f5f08b9593eda88944e67c6bf34ee9ec5a3c3",
        "request_body": "77bc5ad4770db61ee495fe722f365e188c801f1bb2912fccc8227dc96e7b304d",
        "response_schema": "022937e0a4810325254de1264ebd014d5e0628847fb709d9357ad8384babdd54",
        "success_status": "1ff6a8c1e082b093f5089e9707eb0c1cef4e68588706a31618dd23c9503a60bd",
    },
    "session_create": {
        "auth": "56301de7c15754be7e8840f0febb933b17a41314e18dfb6cfb2e7f2990fa70cd",
        "fixed_query": "d3150530e1c3d5a787e6f9dbe4f4f5cbb37ec504acf0803e348e25efc13ad51e",
        "method": "b1db1a3c65e025e0b8c99decc75b6d4917078e9b52353fae1d780f9f38a8daee",
        "operation_id": "a59085e67b138b9cdb8a2fa5155484ebcbe5bd76eaf240da70ad2c15bfd57be7",
        "optional_query_params": "04009699771963b8424afd5c220494cee56233f2777e2fd99556eff973213efc",
        "path": "6b968878e7bfbfdfd2670256e9b029829ecda747ce87ff5377da4601222cc243",
        "path_params": "fca9d91e798f7386567a0e9b98c0e2b1fb51de78e2b5f479c3e7ef1f715ed100",
        "request_body": "536e0753c6924109734e4b2c89b9bbf94f856822a7134f8753f2d6849784536c",
        "response_schema": "2941bf8f914b0a1a0daf285e252214ed2cc31a16fc8f57448421c11b86f37843",
        "success_status": "bba8bf92881c64e5557364baefac9a698d2334fe3cc0f12a169ccc4e3670ddc2",
    },
    "session_delete": {
        "auth": "2a8f3e32b7cb8cd8527df0bda4ea5a53d78691a84ba9b3488c04be4b34147126",
        "fixed_query": "002c649646a971fa9ca24633867b0d48d6ccaf99977b49cfbb8c4e83dddd534f",
        "method": "9ea15b7bc2bef3c207eacd356553086d3ec96d64c27ea899b9a0d9dc2bfafce3",
        "operation_id": "41a815b9e063a243b5aaa76b0fede9f1b13f3184c62f8e5b193608ff541b1785",
        "optional_query_params": "cb87f25b87b73baca274c62a12ad82d9f7efd287de70cb6c661dfb5a365ccc54",
        "path": "9ace54871bc7abeb3414299d076ded44164dac0aa2cafd29804def18bd5aa0ae",
        "path_params": "2fdc976147e30bcf8a933f2f3913f1db7824fb156e8a33423bcfe4e9fe3f3349",
        "request_body": "55e71159dd3184abcf4cb83feddc9e6a935c0cc7f8b6d0a2a6468808c6bc8384",
        "response_schema": "81d2d0c3290d77f38fb199152cf9c234d16c5f19fb7bee864b1e2de0b8b19669",
        "success_status": "16aa56af407bfadf6658e91609dea04d818d55e429baac5277f0b27b996f5ca1",
    },
    "snapshot_create": {
        "auth": "4d052916ea1d1840e60f81f0f4480231e4505c24123e0825ddfe5ca639e03fe5",
        "fixed_query": "70cc49b433fe1571baaf1d658788a147c5ba8d2a1808a42f5ab9bcdaba92b060",
        "method": "5e81d2a8e34f426667694b49c3536e025180222031c71999358cff41a9bf617a",
        "operation_id": "a1a51a65bfdd900153a05db27ca7c15a78b5de8925df109b524ef134952cb9ab",
        "optional_query_params": "363f4279a48839228a84999ebb58af6457b5c28ae0ce3178bcf27a865ed332d8",
        "path": "5f307f7dfc755c17faf6320ce3091c002646df04ff82599512370972b230480a",
        "path_params": "7538d11b056f786905272605f2ec970a8730b2b22bad42a7b6bee393001fbeb5",
        "request_body": "637a1fbacc342282bb4770eea68f1ff27537abfd1108c9de691fc165b7e341d5",
        "response_schema": "4223a75cafd2ebbfcafb09446ababc2d19f7669be32291bc4e70b1de8ef8aaf7",
        "success_status": "146d4e8a3f5efeebef6ca6cc84ca999710ce781d3ed00188c42723335e10e675",
    },
    "task_get": {
        "auth": "238b9767950ef56ce9450f45cf9ce68ef1a7f7378a434f250e561bec1c652786",
        "fixed_query": "3ee86484d04e8f11bdc6f5ecc4db82eb04b3251546977e303e19d5fed2bc8563",
        "method": "308bba839b1403d4a8b35b2793a9d65c64cc621a2fba9096d25f33ceb2b84229",
        "operation_id": "f433e363ae83522e5f50ef69f92d0ab94a484c27dbabd5977ad553907eee48f4",
        "optional_query_params": "b49800f29f9badd317ccff8dccf06eeac6d1de83660d0823d8e58921d9e8c750",
        "path": "18587079cfc53e34d8e31ae4b99e7d2841b838af93e323039f6c09d436bc7153",
        "path_params": "7060d6dd74826470affacec67308d6050bd87601a397760f73572a3f7087b350",
        "request_body": "64286bc981f1c7c0ab95603c6ec9ce489b62933f488863a0f8598f2e0563c9ef",
        "response_schema": "7dc2f831043c530443599b81b1d4c63365b149f341d8619a6feda68c088538a2",
        "success_status": "99081cea0866b1cf280b4ed1585dbad08db9188d8e42f9c036e1df2733a29c64",
    },
    "wire": {
        "create_body_keys_with_retention": "ab2e7c54714dee224f20c549e57feb5e657ca7c8ef24c11e723741bee2ea298b",
        "create_body_keys_without_retention": "e0fd1851a86735bf809838b0533c56aa2e2fee83d4f25caa93678108092f4135",
        "create_query": "d387510fdf031f9b85db703d65b4b7fd16f8c4cf430fcbe7c62c9e873ac98f07",
        "retention_keys": "81229a09ad3578cebb41a780d91c5a4a05274d0f45739eefba7739d7a3d56f6e",
    },
}


def digest(role, field, value):
    return hashlib.sha256(
        ("%s|%s|%s|%s" % (SALT, role, field, value)).encode("utf-8")
    ).hexdigest()


def read_json(path, what):
    if not os.path.exists(path):
        raise AssertionError("%s is missing: %s" % (what, os.path.relpath(path, ROOT)))
    with open(path, encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError as exc:
            raise AssertionError(
                "%s is not valid JSON (%s)" % (os.path.relpath(path, ROOT), exc)
            )


def canonical_operation(entry):
    """Canonical strings for the digest comparison."""
    body = entry.get("request_body")
    if body is None:
        body_text = "null"
    else:
        body_text = "%s|%s|%s" % (
            body.get("schema"),
            ",".join(sorted(str(f) for f in body.get("required", []))),
            ",".join(sorted(str(f) for f in body.get("optional", []))),
        )
    response = entry.get("response_schema")
    return {
        "operation_id": entry.get("operation_id"),
        "method": str(entry.get("method", "")).upper(),
        "path": entry.get("path"),
        "fixed_query": "&".join(
            "%s=%s" % (key, value)
            for key, value in sorted((entry.get("fixed_query") or {}).items())
        ),
        "path_params": ",".join(sorted(str(p) for p in entry.get("path_params", []))),
        "optional_query_params": ",".join(
            sorted(str(p) for p in entry.get("optional_query_params", []))
        ),
        "success_status": str(entry.get("success_status")),
        "auth": entry.get("auth"),
        "request_body": body_text,
        "response_schema": "null" if response is None else str(response),
    }


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Scenario:
    """One mock instance plus one run of the client against it."""

    def __init__(self, retention=None, contract_document=None, fixture_document=None):
        self.retention = retention
        self.tmp = tempfile.TemporaryDirectory(prefix="vcf91-0245-")
        self.log_path = os.path.join(self.tmp.name, "requests.jsonl")
        self.port_path = os.path.join(self.tmp.name, "port")
        self.result_path = os.path.join(self.tmp.name, "result.json")
        self.contract_path = CONTRACT_PATH
        if contract_document is not None:
            self.contract_path = os.path.join(self.tmp.name, "contract.json")
            with open(self.contract_path, "w", encoding="utf-8") as handle:
                json.dump(contract_document, handle)
        self.fixture_path = FIXTURES_PATH
        if fixture_document is not None:
            self.fixture_path = os.path.join(self.tmp.name, "fixtures.json")
            with open(self.fixture_path, "w", encoding="utf-8") as handle:
                json.dump(fixture_document, handle)
        self.mock = None

    def start_mock(self):
        self.mock = subprocess.Popen(
            [
                sys.executable,
                MOCK_PATH,
                "--contract", self.contract_path,
                "--fixtures", self.fixture_path,
                "--log", self.log_path,
                "--port", "0",
                "--port-file", self.port_path,
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.mock.poll() is not None:
                _, err = self.mock.communicate()
                raise AssertionError(
                    "the mock refused to start against docs/contract.json:\n%s" % err.strip()
                )
            if os.path.exists(self.port_path):
                with open(self.port_path, encoding="utf-8") as handle:
                    text = handle.read().strip()
                if text:
                    return int(text)
            time.sleep(0.05)
        raise AssertionError("the mock did not report a listening port within 20s")

    def run_client(self, credentials, expected_returncode=0):
        port = self.start_mock()
        command = [
            sys.executable, "-m", "vsandp",
            "--host-url", "http://127.0.0.1:%d" % port,
            "--username", credentials["username"],
            "--password", credentials["password"],
            "--cluster", CLUSTER,
            "--snapshot-name-prefix", PREFIX,
            "--contract", self.contract_path,
            "--output", self.result_path,
            "--poll-interval", "0",
        ]
        if self.retention:
            command += [
                "--retention-unit", self.retention[0],
                "--retention-duration", str(self.retention[1]),
            ]
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        try:
            completed = subprocess.run(
                command, cwd=ROOT, env=env, text=True, timeout=RUN_TIMEOUT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except subprocess.TimeoutExpired:
            raise AssertionError(
                "`python -m vsandp` did not finish within %ds -- a session refresh loop that "
                "never converges looks exactly like this" % RUN_TIMEOUT
            )
        finally:
            self.stop_mock()
        if completed.returncode != expected_returncode:
            raise AssertionError(
                "`python -m vsandp` exited with %d, expected %d\n--- stdout ---\n%s"
                "\n--- stderr ---\n%s"
                % (completed.returncode, expected_returncode, completed.stdout.strip(),
                   completed.stderr.strip())
            )
        return completed

    def stop_mock(self):
        if self.mock is not None and self.mock.poll() is None:
            self.mock.send_signal(signal.SIGTERM)
            try:
                self.mock.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self.mock.kill()

    def entries(self):
        with open(self.log_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def result(self):
        return read_json(self.result_path, "the client's --output document")

    def cleanup(self):
        self.stop_mock()
        self.tmp.cleanup()


def body_of(entry):
    if entry.get("body_raw") in (None, ""):
        return None
    return json.loads(entry["body_raw"])


def empty_values(document, path="body"):
    """Every location inside *document* holding null, "", {} or []."""
    found = []
    if isinstance(document, dict):
        for key, value in document.items():
            where = "%s.%s" % (path, key)
            if value is None or value == "" or value == {} or value == []:
                found.append(where)
            else:
                found.extend(empty_values(value, where))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            found.extend(empty_values(value, "%s[%d]" % (path, index)))
    return found


class SourcesTest(unittest.TestCase):
    def test_official_sources_document(self):
        sources = read_json(SOURCES_PATH, "docs/official_sources.json")
        for key in ("repository", "commit_sha", "spec_path", "license", "operation_ids"):
            self.assertIn(key, sources, "docs/official_sources.json must record %r" % key)

        self.assertEqual(
            sources["repository"].rstrip("/").removesuffix(".git"),
            SPEC_REPOSITORY,
            "docs/official_sources.json must cite the vmware/vcf-api-specs repository",
        )
        self.assertEqual(
            sources["spec_path"], SPEC_PATH,
            "docs/official_sources.json must record the path of the vSAN Data Protection "
            "OpenAPI document inside that repository",
        )
        self.assertEqual(
            sources["commit_sha"], SPEC_COMMIT,
            "commit_sha must name the immutable OpenAPI revision specified by the task",
        )
        self.assertEqual(
            sources.get("spec_blob_sha"), SPEC_BLOB_SHA,
            "spec_blob_sha must be the git blob sha of the specification file as published "
            "in the repository (it pins the exact file content you derived the contract from)",
        )
        self.assertEqual(
            str(sources["license"]).strip(), "Apache-2.0",
            "record the licence the specification is published under",
        )

        contract = read_json(CONTRACT_PATH, "docs/contract.json")
        self.assertEqual(
            sorted(sources["operation_ids"]), sorted(contract.get("operations", {})),
            "docs/official_sources.json must list exactly the operationIds that "
            "docs/contract.json names",
        )


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.contract = read_json(CONTRACT_PATH, "docs/contract.json")

    def test_contract_shape(self):
        self.assertIn("spec", self.contract)
        self.assertIn("security_schemes", self.contract)
        operations = self.contract.get("operations")
        self.assertIsInstance(operations, dict, "docs/contract.json needs an 'operations' object")
        self.assertEqual(
            len(operations), len(ROLE_LABELS),
            "the contract must name exactly the %d operations the sweep uses, one per role"
            % len(ROLE_LABELS),
        )
        roles = [entry.get("role") for entry in operations.values()]
        self.assertEqual(
            sorted(r for r in roles if r), sorted(ROLE_LABELS),
            "every operation needs a distinct 'role' from: %s" % ", ".join(sorted(ROLE_LABELS)),
        )

    def test_global_facts_match_specification(self):
        base = self.contract.get("spec", {}).get("server_base_path")
        self.assertEqual(
            digest("*", "server_base_path", base),
            DIGESTS["*"]["server_base_path"],
            "spec.server_base_path does not match the path component of the server URL "
            "declared by the OpenAPI document",
        )
        schemes = self.contract.get("security_schemes", {})
        self.assertEqual(
            digest("*", "security_scheme_names", ",".join(sorted(schemes))),
            DIGESTS["*"]["security_scheme_names"],
            "security_schemes must hold exactly the schemes the five operations reference, "
            "named as the specification names them",
        )
        for name, expected in (
            ("api_key_auth", "scheme:api_key_auth"),
            ("basic_auth", "scheme:basic_auth"),
        ):
            scheme = schemes.get(name, {})
            if scheme.get("type") == "apiKey":
                canonical = "apiKey|%s|%s" % (scheme.get("in"), scheme.get("name"))
            else:
                canonical = "%s|%s" % (scheme.get("type"), scheme.get("scheme"))
            self.assertEqual(
                digest("*", expected, canonical), DIGESTS["*"][expected],
                "security_schemes[%r] does not match its declaration in the specification "
                "(type / location / header name)" % name,
            )

    def test_operations_match_specification(self):
        by_role = {}
        for operation_id, entry in self.contract.get("operations", {}).items():
            enriched = dict(entry)
            enriched["operation_id"] = operation_id
            by_role[entry.get("role")] = enriched

        for role, label in sorted(ROLE_LABELS.items()):
            with self.subTest(role=role):
                entry = by_role.get(role)
                self.assertIsNotNone(entry, "no operation carries role %r (%s)" % (role, label))
                canonical = canonical_operation(entry)
                for field in OPERATION_FIELDS:
                    self.assertEqual(
                        digest(role, field, canonical[field]),
                        DIGESTS[role][field],
                        "role %r (%s): %r does not match the specification; you declared %r"
                        % (role, label, field, canonical[field]),
                    )


class PackageTest(unittest.TestCase):
    def sources(self):
        for base, _, names in os.walk(PACKAGE_DIR):
            for name in sorted(names):
                if name.endswith(".py"):
                    yield os.path.join(base, name)

    def test_package_is_stdlib_only(self):
        self.assertTrue(os.path.isdir(PACKAGE_DIR), "the vsandp package is missing")
        allowed = set(sys.stdlib_module_names) | {"vsandp"}
        for path in self.sources():
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [] if node.level else [(node.module or "").split(".")[0]]
                else:
                    continue
                for name in names:
                    self.assertIn(
                        name, allowed,
                        "%s imports %r, which is not in the standard library"
                        % (os.path.relpath(path, ROOT), name),
                    )

    def test_client_routes_from_the_contract(self):
        for path in self.sources():
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn(
                "/snapservice", text,
                "%s hard-codes an API path; the client must build every request from "
                "docs/contract.json" % os.path.relpath(path, ROOT),
            )


class RuntimeContractTest(unittest.TestCase):
    def test_every_request_fact_is_loaded_from_the_runtime_contract(self):
        document = json.loads(json.dumps(read_json(CONTRACT_PATH, "docs/contract.json")))
        document["spec"]["server_base_path"] = "/runtime-api"
        document["security_schemes"]["api_key_auth"]["name"] = "x-runtime-session"
        methods = {
            "session_create": "PUT",
            "session_delete": "PATCH",
            "protection_groups_list": "POST",
            "snapshot_create": "PUT",
            "task_get": "POST",
        }
        for entry in document["operations"].values():
            entry["method"] = methods[entry["role"]]
            entry["path"] = "/remapped" + entry["path"]
            entry["success_status"] = 226
            if entry["role"] == "snapshot_create":
                entry["fixed_query"] = {"dispatch": "task"}

        fixtures = read_json(FIXTURES_PATH, "mock/fixtures/site.json")
        scenario = Scenario(contract_document=document)
        try:
            scenario.run_client(fixtures["credentials"])
            log = scenario.entries()
        finally:
            scenario.cleanup()

        self.assertTrue(log)
        for entry in log:
            self.assertTrue(entry["path"].startswith("/runtime-api/remapped/"))
            self.assertEqual(entry["method"], methods[entry["role"]])
            self.assertIn(entry["status"], (226, 401))
            if entry["role"] not in ("session_create",):
                self.assertEqual(entry["session_header_name"], "x-runtime-session")
        creates = [entry for entry in log if entry["role"] == "snapshot_create"]
        self.assertTrue(creates)
        self.assertTrue(all(entry["raw_query"] == "dispatch=task" for entry in creates))


class FailedSweepTest(unittest.TestCase):
    def test_a_terminal_failure_is_reported_and_exits_nonzero(self):
        fixtures = json.loads(json.dumps(read_json(FIXTURES_PATH, "mock/fixtures/site.json")))
        fixtures["task_terminal_status"] = "FAILED"
        scenario = Scenario(fixture_document=fixtures)
        try:
            scenario.run_client(fixtures["credentials"], expected_returncode=1)
            report = scenario.result()
            log = scenario.entries()
        finally:
            scenario.cleanup()

        results = report.get("protection_groups")
        self.assertEqual(len(results), 4)
        self.assertTrue(all(entry.get("status") == "FAILED" for entry in results))
        self.assertTrue(all(entry.get("snapshot") is None for entry in results))
        self.assertEqual(log[-1]["role"], "session_delete")
        self.assertEqual(log[-1]["status"], 204)


class SweepTestMixin:
    retention = None

    @classmethod
    def setUpClass(cls):
        cls.fixtures = read_json(FIXTURES_PATH, "mock/fixtures/site.json")
        cls.groups = cls.fixtures["clusters"][CLUSTER]["protection_groups"]
        cls.scenario = Scenario(retention=cls.retention)
        try:
            cls.completed = cls.scenario.run_client(cls.fixtures["credentials"])
            cls.log = cls.scenario.entries()
            cls.report = cls.scenario.result()
        except BaseException:
            cls.scenario.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.scenario.cleanup()

    # -- helpers ------------------------------------------------------------ #

    def role_entries(self, role, status=None):
        return [
            entry for entry in self.log
            if entry["role"] == role and (status is None or entry["status"] == status)
        ]

    # -- the run reached its goal ------------------------------------------- #

    def test_10_every_protection_group_was_snapshotted(self):
        report = self.report
        self.assertEqual(report.get("cluster"), CLUSTER)
        entries = report.get("protection_groups")
        self.assertIsInstance(entries, list, "the report needs a 'protection_groups' list")
        self.assertEqual(
            [entry.get("pg") for entry in entries],
            [group["pg"] for group in self.groups],
            "report every protection group of the cluster, in the order the appliance listed them",
        )
        for index, (entry, group) in enumerate(zip(entries, self.groups), start=1):
            self.assertEqual(entry.get("status"), "SUCCEEDED", "task for %s did not succeed" % entry.get("pg"))
            self.assertEqual(entry.get("task"), "task-%08d" % index)
            self.assertEqual(entry.get("snapshot"), "pgsnap-%08d" % index,
                             "carry the task result (the new snapshot identifier) into the report")
            self.assertEqual(
                entry.get("snapshot_name"), "%s-%s" % (PREFIX, group["info"]["name"]),
                "the snapshot name must be the prefix joined to the protection group name",
            )

    def test_11_session_refresh_is_reported(self):
        creates = self.role_entries("session_create", status=201)
        self.assertGreaterEqual(
            len(creates), 2,
            "the token expires part way through this sweep, so the run must create more "
            "than one session",
        )
        self.assertEqual(self.report.get("session_creates"), len(creates))
        self.assertEqual(self.report.get("session_refreshes"), len(creates) - 1)

    # -- wire shape --------------------------------------------------------- #

    def test_20_only_contract_operations_were_called(self):
        contract = read_json(CONTRACT_PATH, "docs/contract.json")
        base = contract["spec"]["server_base_path"]
        for entry in self.log:
            self.assertIsNotNone(
                entry["role"],
                "%s %s does not correspond to any operation in the contract"
                % (entry["method"], entry["target"]),
            )
            self.assertTrue(entry["path"].startswith(base + "/"))
            self.assertLess(entry["status"], 500, "the appliance reported a server error")
            self.assertIn(
                entry["status"], (200, 201, 202, 204, 401),
                "unexpected %d for %s %s -- the only failure this sweep may provoke is the "
                "expiry of the session token" % (entry["status"], entry["method"], entry["target"]),
            )

    def test_21_credentials_are_used_only_to_open_a_session(self):
        for entry in self.log:
            if entry["role"] == "session_create":
                self.assertEqual(entry["authorization_scheme"], "basic",
                                 "opening a session must present the HTTP Basic credentials")
                self.assertIsNone(entry["session_token"],
                                  "do not send a session token when opening a session")
            else:
                self.assertIsNone(
                    entry["authorization_scheme"],
                    "%s must authenticate with the session token, not with the credentials"
                    % entry["role"],
                )
                self.assertTrue(
                    entry["session_token"],
                    "%s must carry the session token header" % entry["role"],
                )

    def test_22_requests_carry_no_stray_query_parameters(self):
        for entry in self.log:
            if entry["role"] == "snapshot_create":
                self.assertEqual(
                    digest("wire", "create_query", entry["raw_query"]),
                    DIGESTS["wire"]["create_query"],
                    "the snapshot operation's query string must be exactly the one the "
                    "specification builds into the operation, and nothing else; you sent %r"
                    % entry["raw_query"],
                )
            else:
                self.assertEqual(
                    entry["raw_query"], "",
                    "%s must be sent without a query string: optional filters that are not "
                    "set are omitted, not sent empty (you sent %r)"
                    % (entry["role"], entry["raw_query"]),
                )

    def test_23_snapshot_bodies_omit_unset_optional_fields(self):
        expected_key = (
            "create_body_keys_with_retention" if self.retention
            else "create_body_keys_without_retention"
        )
        creates = self.role_entries("snapshot_create")
        self.assertTrue(creates, "no snapshot was requested")
        for entry in creates:
            self.assertEqual(
                (entry["content_type"] or "").split(";")[0].strip(), "application/json",
                "send the snapshot specification as application/json",
            )
            body = body_of(entry)
            self.assertIsInstance(body, dict, "the snapshot specification must be a JSON object")
            self.assertEqual(
                digest("wire", expected_key, ",".join(sorted(body))),
                DIGESTS["wire"][expected_key],
                "the snapshot specification carries the wrong set of properties for this run "
                "(you sent: %s); an optional property that is not set must be left out entirely"
                % (", ".join(sorted(body)) or "no properties"),
            )
            self.assertEqual(
                empty_values(body), [],
                "the snapshot specification must not contain null or empty values: %s"
                % ", ".join(empty_values(body)),
            )
            if self.retention:
                nested = [value for value in body.values() if isinstance(value, dict)]
                self.assertEqual(len(nested), 1, "the retention period must be a nested object")
                self.assertEqual(
                    digest("wire", "retention_keys", ",".join(sorted(nested[0]))),
                    DIGESTS["wire"]["retention_keys"],
                    "the retention period object carries the wrong set of properties "
                    "(you sent %s)" % ", ".join(sorted(nested[0])),
                )
                self.assertEqual(set(nested[0].values()), {self.retention[0], self.retention[1]},
                                 "the retention period must carry the requested unit and duration")

    def test_24_bodies_are_sent_only_where_the_contract_declares_one(self):
        contract = read_json(CONTRACT_PATH, "docs/contract.json")
        bodied = {
            entry["role"] for entry in contract["operations"].values()
            if entry.get("request_body") is not None
        }
        for entry in self.log:
            if entry["role"] in bodied:
                self.assertTrue(entry["body_raw"], "%s must carry a body" % entry["role"])
            else:
                self.assertIn(
                    entry["body_raw"], (None, ""),
                    "%s takes no request body; sending an empty object is still sending a body"
                    % entry["role"],
                )

    # -- the refresh must not throw work away -------------------------------- #

    def test_30_expiry_is_recovered_by_retrying_the_same_request(self):
        indexes = [i for i, entry in enumerate(self.log) if entry["status"] == 401]
        self.assertTrue(
            indexes,
            "no request was ever rejected with 401: use the session token until the "
            "appliance stops accepting it and recover from that, rather than cycling "
            "sessions pre-emptively",
        )
        successful_logins = [
            i for i, entry in enumerate(self.log)
            if entry["role"] == "session_create" and entry["status"] == 201
        ]
        for index in successful_logins[1:]:
            self.assertEqual(
                self.log[index - 1]["status"], 401,
                "a replacement session may be opened only after the appliance rejects "
                "the session that was in use",
            )
        for index in indexes:
            failed = self.log[index]
            tail = self.log[index + 1:]
            self.assertTrue(tail, "the run stopped at the first expired token instead of "
                                  "opening a new session")
            self.assertEqual(
                tail[0]["role"], "session_create",
                "an expired token must be answered by opening a new session, not by %r"
                % tail[0]["role"],
            )
            self.assertGreaterEqual(
                len(tail), 2,
                "the run opened a replacement session but never replayed the rejected call",
            )
            replay = tail[1]
            self.assertEqual(
                (replay["role"], replay["method"], replay["path"], replay["raw_query"],
                 replay["body_raw"]),
                (failed["role"], failed["method"], failed["path"], failed["raw_query"],
                 failed["body_raw"]),
                "the request rejected with 401 must be the very next resource call replayed "
                "after the replacement session is opened",
            )
            self.assertNotEqual(
                replay["status"], 401,
                "the replay with the replacement session must be accepted",
            )

    def test_31_completed_work_is_not_repeated(self):
        listings = self.role_entries("protection_groups_list", status=200)
        self.assertEqual(
            len(listings), 1,
            "the protection groups were listed %d times; refreshing the session must not "
            "restart the sweep from the beginning" % len(listings),
        )
        creates = self.role_entries("snapshot_create", status=202)
        self.assertEqual(
            len(creates), len(self.groups),
            "expected exactly one accepted snapshot request per protection group",
        )
        self.assertEqual(
            len({entry["path"] for entry in creates}), len(self.groups),
            "two snapshot requests targeted the same protection group",
        )
        self.assertEqual(
            [entry for entry in self.log if entry["role"] == "snapshot_create"
             and entry["status"] == 400], [],
            "the appliance rejected a snapshot request because one was already in progress "
            "for that protection group -- work submitted before the refresh was submitted twice",
        )

    def test_32_tasks_are_polled_to_completion_and_no_further(self):
        polls = self.role_entries("task_get", status=200)
        per_task = {}
        for entry in polls:
            per_task.setdefault(entry["path"], 0)
            per_task[entry["path"]] += 1
        self.assertEqual(
            len(per_task), len(self.groups),
            "every submitted task must be followed to completion",
        )
        expected = self.fixtures["task_polls_before_success"]
        for path, count in sorted(per_task.items()):
            self.assertEqual(
                count, expected,
                "%s was polled %d times; poll until the task reports a terminal state and "
                "then stop" % (path.rsplit("/", 1)[-1], count),
            )

    def test_33_the_session_in_use_is_closed_at_the_end(self):
        last = self.log[-1]
        self.assertEqual(last["role"], "session_delete",
                         "close the session once the sweep is done")
        self.assertEqual(last["status"], 204)
        creates = self.role_entries("session_create", status=201)
        self.assertEqual(
            last["session_token"], "sess-%08d" % len(creates),
            "log out with the session that is actually in use -- the newest one",
        )
        self.assertEqual(
            len(creates), self.report["session_creates"],
            "the report must count every session the run opened",
        )


class SweepWithoutRetentionTest(SweepTestMixin, unittest.TestCase):
    retention = None


class SweepWithRetentionTest(SweepTestMixin, unittest.TestCase):
    retention = ("DAY", 14)


if __name__ == "__main__":
    unittest.main(verbosity=2)
