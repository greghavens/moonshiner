#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0285.

Starts the contract-pinned loopback mock, drives the candidate module through
tests/exercise.ps1, then asserts the emitted results and the exact request wire
shape recorded by the mock. No live VMware endpoint is contacted.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl

ROOT = Path(__file__).resolve().parents[1]

SOURCE_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SOURCE_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
SDK_MODULE = "VMware.Sdk.Vcf.Ops"
SDK_ASSEMBLY = "VMware.Bindings.Vcf.Ops"
SDK_ERROR_TYPE = "VMware.Bindings.Vcf.Ops.Model.ApiError"

EXPECTED_OPERATIONS = {
    "create": ("POST", "/api/ni/auth/token"),
    "listApplications": ("GET", "/api/ni/groups/applications"),
    "getApplicationById": ("GET", "/api/ni/groups/applications/{id}"),
}

USERNAME = "admin@local"
PASSWORD = "VMware1!VMware1!"

# Ordinal ascending by name, ties broken by ordinal ascending entity_id.
EXPECTED_ORDER = [
    ("18230:561:271275768", "APP-PROD", 1, 7),
    ("18230:561:271275766", "App-Prod", 2, 18),
    ("18230:561:271275771", "Zeta", 2, 33),
    ("18230:561:271275772", "alpha", 7, 64),
    ("18230:561:271275767", "app-prod", 5, 96),
    ("18230:561:271275769", "billing", 4, 55),
    ("18230:561:271275770", "billing", 6, 12),
    ("18230:561:271275765", "web-tier", 3, 41),
]
ALL_IDS = sorted(entry[0] for entry in EXPECTED_ORDER)

PAGED_CURSORS = [None, "Mw==", "Ng=="]  # base64 of offsets 0(absent), 3, 6
SESSION_TOKEN = "ni-token-0001"
DETAIL_FAILURE_MODIFIED_AFTER = "1700000000001"
REPEATED_CURSOR_MODIFIED_AFTER = "1700000000002"
MISSING_APPLICATION_ID = "18230:561:999999999"
HELPER_PREFIX_SHA256 = "34d2832a658b32e55aa133c7efdd81b688e05f01239490ae2df273c1e0218a22"


def fail(message: str) -> None:
    raise AssertionError(message)


# --------------------------------------------------------------------------
# Static checks
# --------------------------------------------------------------------------

def verify_contract() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs/official_sources.json").read_text(encoding="utf-8"))

    if contract["openapiVersion"] != "3.0.1":
        fail("contract OpenAPI version is not 3.0.1")
    if contract["apiVersion"] != "9.1.0.0":
        fail("contract API version is not 9.1.0.0")
    if contract["serverBasePath"] != "/api/ni":
        fail("contract server base path is not /api/ni")
    if contract["derivedFrom"]["commitSha"] != SOURCE_SHA:
        fail("contract source commit changed")
    if contract["derivedFrom"]["specPath"] != SOURCE_PATH:
        fail("contract source path changed")

    actual = {
        operation_id: (definition["method"], definition["absolutePath"])
        for operation_id, definition in contract["operations"].items()
    }
    if actual != EXPECTED_OPERATIONS:
        fail(f"contract operation map changed: {actual!r}")
    if contract["operationIds"] != list(EXPECTED_OPERATIONS):
        fail("contract operationId order or contents changed")

    if contract["operations"]["create"]["security"] != []:
        fail("the create operation must have empty security")
    if contract["securitySchemes"]["ApiKeyAuth"]["valueFormat"] != "NetworkInsight {token}":
        fail("ApiKeyAuth header format changed")

    list_params = {
        parameter["name"]: parameter
        for parameter in contract["operations"]["listApplications"]["parameters"]
    }
    if set(list_params) != {"size", "cursor", "modifiedAfter"}:
        fail(f"listApplications parameter set changed: {sorted(list_params)!r}")
    if any(parameter["required"] for parameter in list_params.values()):
        fail("no listApplications query parameter is required by the specification")

    credential = contract["schemas"]["UserCredential"]
    if set(credential["properties"]) != {"username", "password", "domain"}:
        fail("UserCredential property set changed")
    if set(contract["schemas"]["Domain"]["properties"]) != {"domain_type", "value"}:
        fail("Domain property set changed")
    if set(contract["schemas"]["ApiError"]["properties"]) != {"code", "message", "details"}:
        fail("ApiError property set changed")

    # provenance
    if sources["commitSha"] != SOURCE_SHA or sources["specPath"] != SOURCE_PATH:
        fail("official_sources.json provenance does not match the contract")
    if sources["repository"] != "https://github.com/vmware/vcf-api-specs":
        fail("official_sources.json names the wrong repository")
    if sources["repositoryLicense"] != "Apache-2.0":
        fail("official_sources.json license changed")
    recorded = {entry["operationId"] for entry in sources["operations"]}
    if recorded != set(EXPECTED_OPERATIONS):
        fail(f"official_sources.json operationIds changed: {sorted(recorded)!r}")
    if SOURCE_SHA not in sources["specUrl"]:
        fail("official_sources.json specUrl is not pinned to the recorded commit")
    if sources["prerequisiteModule"]["name"] != SDK_MODULE:
        fail("official_sources.json prerequisite module changed")
    if sources["prerequisiteModule"]["vendored"] is not False:
        fail("the prerequisite module must not be vendored")


BANNED_CLIENTS = (
    "Invoke-RestMethod",
    "Invoke-WebRequest",
    "System.Net.WebClient",
    "New-Object Net.WebClient",
    "curl ",
    "wget ",
)


def verify_manifest_and_no_vendoring() -> None:
    manifest = (ROOT / "src/VcfOpsNetworks/VcfOpsNetworks.psd1").read_text(encoding="utf-8")
    if SDK_MODULE not in manifest:
        fail(f"the module manifest must declare {SDK_MODULE} as a required module")
    if "RequiredModules" not in manifest:
        fail("the module manifest must declare RequiredModules")

    module = (ROOT / "src/VcfOpsNetworks/VcfOpsNetworks.psm1").read_text(encoding="utf-8")

    # The task declares these helpers already implemented and permits work only
    # in the two public functions (plus private support code).  Protect their
    # exact seed implementation so a candidate cannot replace or intercept the
    # genuine VMware OpenAPI transport while preserving the same mock wire.
    field_start = module.find("function Get-NiField")
    helper_end_marker = "    return $null\n}\n"
    helper_end = module.find(helper_end_marker, field_start)
    if field_start < 0 or helper_end < 0:
        fail("the protected transport helper region is missing")
    helper_end += len(helper_end_marker)
    helper_digest = hashlib.sha256(module[:helper_end].encode("utf-8")).hexdigest()
    if helper_digest != HELPER_PREFIX_SHA256:
        fail("the already-implemented transport helpers must remain unchanged")
    for helper_name in (
        "New-NiApiConnection",
        "New-NiRequestOptions",
        "Invoke-NiRequest",
        "Get-NiField",
    ):
        definitions = re.findall(
            rf"(?im)^\s*function\s+(?:\w+:)?{re.escape(helper_name)}\b",
            module,
        )
        if len(definitions) != 1:
            fail(f"the protected helper {helper_name} must have exactly one definition")

    for banned in BANNED_CLIENTS:
        if banned in module:
            fail(
                f"the module must issue requests through the PowerCLI OpenAPI "
                f"binding layer of the prerequisite, not {banned.strip()!r}"
            )
    if "VMware.Binding.OpenApi.Client.ApiClient" not in module:
        fail(
            "the module must build its transport on "
            "VMware.Binding.OpenApi.Client.ApiClient from the prerequisite"
        )

    for path in ROOT.rglob("*"):
        name = path.name
        if path.is_dir() and name.startswith("VMware.Sdk.Vcf"):
            fail(f"the prerequisite SDK must not be vendored: {path}")
        if path.is_file() and name.lower().endswith(".dll") and "vmware" in name.lower():
            fail(f"the prerequisite SDK must not be vendored: {path}")


# --------------------------------------------------------------------------
# Integration
# --------------------------------------------------------------------------

def run_integration() -> tuple[dict, list[dict]]:
    python = shutil.which("python3")
    pwsh = shutil.which("pwsh")
    if python is None or pwsh is None:
        fail("python3 and pwsh are required environment prerequisites")

    with tempfile.TemporaryDirectory(prefix="vcf91-0285-") as temporary:
        temp = Path(temporary)
        log_path = temp / "requests.jsonl"
        port_file = temp / "port.txt"
        output_file = temp / "result.json"

        server = subprocess.Popen(
            [
                python, "-B", str(ROOT / "tests/mock_vcf_on.py"),
                "--contract", str(ROOT / "docs/contract.json"),
                "--log", str(log_path),
                "--port-file", str(port_file),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    _, stderr = server.communicate()
                    fail(f"loopback mock exited early: {stderr.decode('utf-8', 'replace')}")
                if port_file.exists():
                    text = port_file.read_text(encoding="utf-8").strip()
                    if text:
                        port = int(text)
                        break
                time.sleep(0.05)
            if port is None:
                fail("loopback mock did not report a port")

            environment = dict(os.environ)
            environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
            environment["VMWARE_POWERCLI_CEIP_OPT_IN"] = "0"

            completed = subprocess.run(
                [
                    pwsh, "-NoProfile", "-NonInteractive",
                    "-File", str(ROOT / "tests/exercise.ps1"),
                    "-Port", str(port),
                    "-OutputFile", str(output_file),
                    "-ModulePath", str(ROOT / "src/VcfOpsNetworks/VcfOpsNetworks.psd1"),
                ],
                capture_output=True,
                timeout=300,
                env=environment,
                cwd=str(ROOT),
            )
            if completed.returncode != 0:
                fail(
                    "the exercise harness failed\n"
                    f"stdout:\n{completed.stdout.decode('utf-8', 'replace')}\n"
                    f"stderr:\n{completed.stderr.decode('utf-8', 'replace')}"
                )
            if not output_file.exists():
                fail("the exercise harness produced no result document")

            results = json.loads(output_file.read_text(encoding="utf-8"))
            records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            return results, records
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()


# --------------------------------------------------------------------------
# Behaviour assertions
# --------------------------------------------------------------------------

def scenario(results: dict, name: str) -> dict:
    scenarios = results.get("scenarios") or {}
    if name not in scenarios:
        fail(f"scenario {name!r} was not recorded")
    return scenarios[name]


def verify_results(results: dict) -> None:
    if results.get("moduleImport") != "ok":
        fail(f"the module did not import: {results.get('moduleImport')!r}")

    for name, expected_token in (
        ("connect_plain", "ni-token-0001"),
        ("connect_ldap", "ni-token-0002"),
        ("connect_local", "ni-token-0003"),
        ("connect_value_only", "ni-token-0004"),
    ):
        entry = scenario(results, name)
        if entry["status"] != "ok":
            fail(f"{name} failed: {entry.get('error')!r}")
        if entry.get("token") != expected_token:
            fail(f"{name} did not surface the issued token: {entry.get('token')!r}")

    plain = scenario(results, "connect_plain")
    if not str(plain.get("baseUri", "")).rstrip("/").endswith("/api/ni"):
        fail(f"the session base URI must target /api/ni: {plain.get('baseUri')!r}")
    if plain.get("expiry") not in (1793491200000, "1793491200000"):
        fail(f"the session did not carry the Token expiry: {plain.get('expiry')!r}")

    expected_records = [
        {
            "entity_id": entity_id,
            "name": name,
            "entity_type": "Application",
            "tier_count": tier_count,
            "member_count": member_count,
        }
        for entity_id, name, tier_count, member_count in EXPECTED_ORDER
    ]

    for name in ("list_paged", "list_default"):
        entry = scenario(results, name)
        if entry["status"] != "ok":
            fail(f"{name} failed: {entry.get('error')!r}")
        applications = entry.get("applications")
        if not isinstance(applications, list):
            fail(f"{name} emitted no application sequence")
        if len(applications) != len(EXPECTED_ORDER):
            fail(
                f"{name} emitted {len(applications)} applications, expected "
                f"{len(EXPECTED_ORDER)}; the paginated collection was not "
                f"retrieved completely"
            )
        normalised = [
            {
                "entity_id": item.get("entity_id"),
                "name": item.get("name"),
                "entity_type": item.get("entity_type"),
                "tier_count": item.get("tier_count"),
                "member_count": item.get("member_count"),
            }
            for item in applications
        ]
        if [item["entity_id"] for item in normalised] != [
            item["entity_id"] for item in expected_records
        ]:
            fail(
                f"{name} emitted the wrong order.\n"
                f"expected: {[item['entity_id'] for item in expected_records]}\n"
                f"actual:   {[item['entity_id'] for item in normalised]}\n"
                "The contract requires ordinal ascending name with an ordinal "
                "entity_id tie-break."
            )
        if normalised != expected_records:
            fail(
                f"{name} emitted objects that do not carry the "
                f"getApplicationById detail fields: {normalised!r}"
            )

    paged = scenario(results, "list_paged")["applications"]
    default = scenario(results, "list_default")["applications"]
    if [item["entity_id"] for item in paged] != [item["entity_id"] for item in default]:
        fail("the emitted order changed with the page size; the order is not stable")

    repeated = scenario(results, "repeated_cursor")
    if repeated["status"] != "threw":
        fail("a repeated cursor must terminate before its page is re-requested")

    verify_api_error(
        scenario(results, "list_failure"),
        "listApplications failure",
        401,
        "Missing or invalid NetworkInsight token",
        1002,
        "Missing or invalid NetworkInsight token",
        ["Authorization"],
    )
    verify_api_error(
        scenario(results, "detail_failure"),
        "getApplicationById failure",
        404,
        f"Application {MISSING_APPLICATION_ID!r} not found",
        1404,
        f"Application {MISSING_APPLICATION_ID!r} not found",
        ["id"],
    )
    verify_api_error(
        scenario(results, "auth_failure"),
        "create failure",
        401,
        "Invalid credentials",
        1001,
        "Authentication failed for user",
        ["username"],
    )


def verify_api_error(
    failure: dict,
    label: str,
    expected_code: int,
    expected_message: str,
    expected_detail_code: int,
    expected_detail_message: str,
    expected_target: list[str],
) -> None:
    if failure["status"] != "threw":
        fail(f"{label} must raise a terminating error")
    if failure.get("targetTypeFullName") != SDK_ERROR_TYPE:
        fail(
            f"{label} must carry a "
            f"{SDK_ERROR_TYPE} on ErrorRecord.TargetObject, got "
            f"{failure.get('targetTypeFullName')!r}"
        )
    if failure.get("targetAssembly") != SDK_ASSEMBLY:
        fail(
            f"{label}: the ApiError model must come from the {SDK_ASSEMBLY} prerequisite "
            f"assembly, got {failure.get('targetAssembly')!r}"
        )
    if failure.get("targetCode") != expected_code:
        fail(f"{label}: ApiError Code was not carried through: {failure.get('targetCode')!r}")
    if failure.get("targetMessage") != expected_message:
        fail(f"{label}: ApiError Message was not carried through: {failure.get('targetMessage')!r}")
    details = failure.get("targetDetails")
    if not isinstance(details, list) or len(details) != 1:
        fail(f"{label}: ApiError Details collection was not carried through: {details!r}")
    detail = details[0]
    if (detail.get("code") != expected_detail_code
            or detail.get("message") != expected_detail_message):
        fail(f"{label}: ApiError detail was not carried through: {detail!r}")
    if list(detail.get("target") or []) != expected_target:
        fail(f"{label}: ApiError detail target was not carried through: {detail!r}")


def query_map(record: dict) -> dict:
    return dict(parse_qsl(record["rawQuery"], keep_blank_values=True))


def verify_wire(records: list[dict]) -> None:
    if not records:
        fail("the mock recorded no requests")

    unknown = [r for r in records if r.get("operationId") is None]
    if unknown:
        first = unknown[0]
        fail(
            f"a request targeted an endpoint outside the contract: "
            f"{first['method']} {first['path']}"
        )

    # ---- create -----------------------------------------------------------
    creates = [r for r in records if r["operationId"] == "create"]
    if len(creates) != 5:
        fail(f"expected exactly five create requests, received {len(creates)}")

    expected_bodies = [
        {"username": USERNAME, "password": PASSWORD},
        {
            "username": USERNAME,
            "password": PASSWORD,
            "domain": {"domain_type": "LDAP", "value": "corp.example.com"},
        },
        {"username": USERNAME, "password": PASSWORD, "domain": {"domain_type": "LOCAL"}},
        {"username": USERNAME, "password": PASSWORD},
        {"username": USERNAME, "password": "wrong-password"},
    ]
    labels = [
        "connect_plain", "connect_ldap", "connect_local",
        "connect_value_only", "auth_failure",
    ]

    for index, record in enumerate(creates):
        label = labels[index]
        if record["rawQuery"]:
            fail(f"{label}: create must not use query parameters")
        headers = record["headers"]
        content_type = str(headers.get("content-type", "")).split(";")[0].strip().lower()
        if content_type != "application/json":
            fail(f"{label}: create Content-Type is not application/json: {content_type!r}")
        if "authorization" in headers:
            fail(
                f"{label}: create has empty security in the specification and must "
                f"not send an Authorization header"
            )
        try:
            body = json.loads(record["body"])
        except json.JSONDecodeError as error:
            fail(f"{label}: create body is not JSON: {error}")
        if body != expected_bodies[index]:
            fail(
                f"{label}: create body has the wrong exact property set or values.\n"
                f"expected: {expected_bodies[index]!r}\nactual:   {body!r}"
            )
        if index in (0, 3, 4) and "domain" in body:
            fail(f"{label}: the unset optional domain object must be omitted, not sent empty")
        if index == 2 and "value" in body.get("domain", {}):
            fail(
                f"{label}: the unset optional Domain.value must be omitted for a "
                f"LOCAL domain, not sent empty"
            )

    if creates[0]["responseStatus"] != 200 or creates[4]["responseStatus"] != 401:
        fail("the mock did not resolve the credential scenarios as expected")

    # ---- listApplications -------------------------------------------------
    lists = [r for r in records if r["operationId"] == "listApplications"]
    if len(lists) != 8:
        fail(
            f"expected exactly eight listApplications requests across success and "
            f"failure scenarios, received {len(lists)}"
        )

    for record in lists:
        params = query_map(record)
        unexpected = sorted(set(params) - {"size", "cursor", "modifiedAfter"})
        if unexpected:
            fail(f"listApplications sent unknown query parameters: {unexpected!r}")
        empty = sorted(key for key, value in params.items() if value == "")
        if empty:
            fail(
                f"listApplications sent unset optional query parameters as empty "
                f"values instead of omitting them: {empty!r}"
            )
        expected_authorization = (
            "NetworkInsight invalid-token"
            if record["responseStatus"] == 401
            else f"NetworkInsight {SESSION_TOKEN}"
        )
        if str(record["headers"].get("authorization")) != expected_authorization:
            fail(
                f"listApplications sent the wrong Authorization header: "
                f"{record['headers'].get('authorization')!r}"
            )
        if record["body"]:
            fail("listApplications must not send a request body")
        if record["responseStatus"] not in (200, 401):
            fail(f"a listApplications request was rejected: {record}")

    paged_lists = [
        r for r in lists
        if query_map(r).get("size") == "3" and "modifiedAfter" not in query_map(r)
    ]
    default_lists = [
        r for r in lists
        if query_map(r).get("size") == "100"
        and query_map(r).get("modifiedAfter") == "1700000000000"
    ]
    if len(paged_lists) != 3:
        fail(
            f"the -PageSize 3 sweep must issue exactly three requests, issued "
            f"{len(paged_lists)}"
        )
    if len(default_lists) != 1:
        fail(
            f"the default sweep must send size=100 exactly once, sent "
            f"{len(default_lists)} such requests"
        )

    for index, record in enumerate(paged_lists):
        params = query_map(record)
        expected_cursor = PAGED_CURSORS[index]
        if expected_cursor is None:
            if "cursor" in params:
                fail(
                    "the first listApplications request of a sweep must omit the "
                    f"cursor parameter, sent cursor={params['cursor']!r}"
                )
        else:
            if params.get("cursor") != expected_cursor:
                fail(
                    f"paged request {index + 1} must echo the cursor from the "
                    f"previous response ({expected_cursor!r}), sent "
                    f"{params.get('cursor')!r}"
                )
        if "modifiedAfter" in params:
            fail("the paged sweep did not bind ModifiedAfter, so it must be omitted")
        if record.get("servedOffset") != index * 3:
            fail(f"paged request {index + 1} did not advance to a fresh page")

    served = [r.get("servedOffset") for r in paged_lists]
    if len(set(served)) != len(served):
        fail(f"the sweep re-requested a page it had already fetched: offsets {served!r}")

    default_params = query_map(default_lists[0])
    if "cursor" in default_params:
        fail("the single-page default sweep must omit the cursor parameter")
    if default_params.get("modifiedAfter") != "1700000000000":
        fail(
            f"the bound ModifiedAfter must be sent verbatim, got "
            f"{default_params.get('modifiedAfter')!r}"
        )

    list_failures = [r for r in lists if r["responseStatus"] == 401]
    if len(list_failures) != 1:
        fail(f"expected one listApplications 401 response, got {len(list_failures)}")

    detail_fixture_lists = [
        r for r in lists
        if query_map(r).get("modifiedAfter") == DETAIL_FAILURE_MODIFIED_AFTER
    ]
    if len(detail_fixture_lists) != 1 or detail_fixture_lists[0]["responseStatus"] != 200:
        fail("the detail-failure fixture did not issue exactly one successful list request")
    if "cursor" in query_map(detail_fixture_lists[0]):
        fail("the detail-failure sweep must omit cursor on its first and only page")

    repeated_lists = [
        r for r in lists
        if query_map(r).get("modifiedAfter") == REPEATED_CURSOR_MODIFIED_AFTER
    ]
    if len(repeated_lists) != 2:
        fail(
            "the repeated-cursor sweep must stop after receiving the repeated "
            f"cursor, before a third page request; received {len(repeated_lists)} requests"
        )
    if [r.get("servedOffset") for r in repeated_lists] != [0, 3]:
        fail(f"the repeated-cursor fixture served unexpected pages: {repeated_lists!r}")
    if [r.get("returnedCursor") for r in repeated_lists] != ["Mw==", "Mw=="]:
        fail("the repeated-cursor fixture did not return the same opaque cursor twice")

    # ---- getApplicationById ----------------------------------------------
    details = [r for r in records if r["operationId"] == "getApplicationById"]
    if len(details) != 2 * len(ALL_IDS) + 1:
        fail(
            f"expected {2 * len(ALL_IDS) + 1} getApplicationById requests (every "
            f"successful entity plus one deterministic 404), received {len(details)}"
        )
    for record in details:
        if record["rawQuery"]:
            fail("getApplicationById must not use query parameters")
        if record["body"]:
            fail("getApplicationById must not send a request body")
        if str(record["headers"].get("authorization")) != f"NetworkInsight {SESSION_TOKEN}":
            fail(
                f"getApplicationById must send Authorization: NetworkInsight "
                f"{SESSION_TOKEN}, got {record['headers'].get('authorization')!r}"
            )
        if record["responseStatus"] not in (200, 404):
            fail(f"a getApplicationById request was rejected: {record}")

    failed_details = [r for r in details if r["responseStatus"] == 404]
    if len(failed_details) != 1 or failed_details[0].get("entityId") != MISSING_APPLICATION_ID:
        fail(f"expected one deterministic getApplicationById 404, got {failed_details!r}")

    observed = sorted(record["entityId"] for record in details if record["responseStatus"] == 200)
    if observed != sorted(ALL_IDS * 2):
        fail(
            "each sweep must resolve every listed entity exactly once; observed "
            f"{observed!r}"
        )

    # ---- nothing else -----------------------------------------------------
    total = len(creates) + len(lists) + len(details)
    if total != len(records):
        fail(f"the mock recorded {len(records) - total} unclassified requests")

    expected_failures = [creates[4], list_failures[0], failed_details[0]]
    unexpected_failures = [
        r for r in records if r["responseStatus"] >= 400 and r not in expected_failures
    ]
    if unexpected_failures:
        first = unexpected_failures[0]
        fail(
            f"the mock rejected a request: {first['method']} {first['path']}"
            f"?{first['rawQuery']} -> {first['responseStatus']}"
        )


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_no_vendoring()
        results, records = run_integration()
        verify_results(results)
        verify_wire(records)
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: vcf91-0285")
    return 0


if __name__ == "__main__":
    sys.exit(main())
