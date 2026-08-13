#!/usr/bin/env python3
"""Protected, deterministic acceptance verifier for vcf90-0003.

Everything below runs against a loopback mock pinned to docs/contract.json. No live
VMware endpoint is contacted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SDK_MODULE = "VMware.Sdk.Vcf.SddcManager"
SDK_VERSION = "13.5.0.25380678"

SOURCE_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SOURCE_TAG = "9.0.0.0"
SOURCE_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
API_VERSION = "9.0.0.0"

# The 9.1.0.0 revision of the same file keeps both operationIds but changes the
# surrounding schemas. Pinning those differences keeps the contract on 9.0.0.0.
NINE_ONE_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
NINE_ONE_ONLY_PROPERTY = "connectivityErrorDetails"

EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "getCredentials": ("GET", "/v1/credentials"),
}
HANDSHAKE_PATH = "/v1/sddc-manager"

# Declaration order of getCredentials query parameters in the pinned specification.
DECLARED_QUERY_PARAMETERS = [
    "resourceName",
    "resourceIp",
    "resourceType",
    "domainName",
    "pageNumber",
    "pageSize",
    "accountType",
]
OPTIONAL_FILTERS = {"resourceName", "resourceIp", "resourceType", "domainName", "accountType"}

RESOURCE_TYPES = ["ESXI", "VCENTER", "PSC", "NSXT_MANAGER", "NSXT_EDGE", "NSX_ALB", "BACKUP"]
ACCOUNT_TYPES = ["USER", "SYSTEM", "SERVICE"]

# The complete collection in the required order: ordinal ascending by resource name,
# then credential type, then username, then credential id.
FULL_INVENTORY = [
    "Vcenter-zz.vrack.vsphere.local|SSO|administrator@vsphere.local|61e45c0a-7f32-4db9-a186-29c503e7b814",
    "esx-01.vrack.vsphere.local|API|vcf-admin|7c2a9f18-3d65-4b07-92ef-1a4c6d8b5023",
    "esx-01.vrack.vsphere.local|SSH|root|1f3b6d40-7a92-4c58-8e01-4b6d9f2a7c13",
    "esx-01.vrack.vsphere.local|SSH|svc-esx01|9e05c7a2-4b83-4106-97fd-2c6a0e5b1d48",
    "esx-02.vrack.vsphere.local|API|vcf-admin|42d81b6e-0c37-4f45-a91d-8b25e6c07a3f",
    "esx-02.vrack.vsphere.local|SSH|root|b6741a0c-8e52-4937-b1a4-0d3f9c26e785",
    "nsx-mgmt.vrack.vsphere.local|API|Admin|B0c34b7f-5a18-4d92-8b60-3f7e2c1a9d84",
    "nsx-mgmt.vrack.vsphere.local|API|Admin|a258e3d9-0b46-4f12-a7d5-8c3e1b09f742",
    "nsx-mgmt.vrack.vsphere.local|API|admin|10c34b7f-5a18-4d92-8b60-3f7e2c1a9d84",
    "vcenter-mgmt.vrack.vsphere.local|SSO|administrator@vsphere.local|5d19f82b-6c04-4e71-93a8-2f7b0d1c5e36",
    "vcenter-wld01.vrack.vsphere.local|SSO|administrator@vsphere.local|3b6e0d54-9f21-4a80-b6c7-5e2d8a413f09",
]
ESXI_MGMT_INVENTORY = FULL_INVENTORY[1:6]
SERVICE_INVENTORY = [FULL_INVENTORY[7]]
ESX01_INVENTORY = FULL_INVENTORY[1:4]

EXPECTED_RESULT = {
    "scenarios": {
        "pageSize4": FULL_INVENTORY,
        "pageSize3": FULL_INVENTORY,
        "pageSize9": FULL_INVENTORY,
        "esxiMgmtDomain": ESXI_MGMT_INVENTORY,
        "serviceAccounts": SERVICE_INVENTORY,
        "singleResource": ESX01_INVENTORY,
        "emptyCollection": [],
    },
    "elementTypeNames": ["VMware.Bindings.Vcf.SddcManager.Model.Credential"],
}

# Exact getCredentials query strings, in order. Unset optional filters are absent
# entirely rather than present with an empty value, and every page of every collection
# is fetched exactly once with no request past the last page.
EXPECTED_QUERIES = [
    "pageNumber=0&pageSize=4",
    "pageNumber=1&pageSize=4",
    "pageNumber=2&pageSize=4",
    "pageNumber=0&pageSize=3",
    "pageNumber=1&pageSize=3",
    "pageNumber=2&pageSize=3",
    "pageNumber=3&pageSize=3",
    "pageNumber=0&pageSize=9",
    "pageNumber=1&pageSize=9",
    "resourceType=ESXI&domainName=mgmt-domain&pageNumber=0&pageSize=2",
    "resourceType=ESXI&domainName=mgmt-domain&pageNumber=1&pageSize=2",
    "resourceType=ESXI&domainName=mgmt-domain&pageNumber=2&pageSize=2",
    "pageNumber=0&pageSize=5&accountType=SERVICE",
    "resourceName=esx-01.vrack.vsphere.local&pageNumber=0&pageSize=2",
    "resourceName=esx-01.vrack.vsphere.local&pageNumber=1&pageSize=2",
    "domainName=no-such-domain&pageNumber=0&pageSize=4",
]


def fail(message: str) -> None:
    raise AssertionError(message)


def parse_query(query: str):
    pairs = []
    for chunk in query.split("&"):
        if not chunk:
            continue
        name, separator, value = chunk.partition("=")
        pairs.append((name, value if separator else None))
    return pairs


def verify_contract() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs/official_sources.json").read_text(encoding="utf-8"))

    derived = contract["derivedFrom"]
    if contract["apiVersion"] != API_VERSION:
        fail(f"contract apiVersion must be {API_VERSION}, found {contract['apiVersion']!r}")
    if derived["commitSha"] != SOURCE_SHA:
        fail("contract is not pinned to the 9.0.0.0 commit of the specification")
    if derived["commitSha"] == NINE_ONE_SHA:
        fail("contract was derived from the 9.1.0.0 revision")
    if derived["tag"] != SOURCE_TAG:
        fail(f"contract tag must be {SOURCE_TAG}")
    if derived["specPath"] != SOURCE_PATH:
        fail(f"contract must be derived from {SOURCE_PATH}")
    if derived["openapi"] != "3.0.1" or derived["license"] != "Apache-2.0":
        fail("contract source metadata changed")

    actual = {
        operation_id: (definition["method"], definition["path"])
        for operation_id, definition in contract["operations"].items()
    }
    if actual != EXPECTED_OPERATIONS:
        fail(f"contract must name exactly the operationIds used: {actual!r}")
    if contract["operationIds"] != list(EXPECTED_OPERATIONS):
        fail("contract operationId order or contents changed")

    credentials = contract["operations"]["getCredentials"]
    declared = [row["name"] for row in credentials["queryParameters"]]
    if declared != DECLARED_QUERY_PARAMETERS:
        fail(f"getCredentials query parameters drifted from the specification: {declared!r}")
    if any(row["required"] for row in credentials["queryParameters"]):
        fail("no getCredentials query parameter is required in this specification revision")
    deprecated = {row["name"] for row in credentials["queryParameters"] if row.get("deprecated")}
    if deprecated != {"resourceIp"}:
        fail(f"resourceIp is the only deprecated query parameter: {deprecated!r}")
    if credentials["successStatus"] != 200 or credentials["successSchema"] != "PageOfCredential":
        fail("getCredentials success contract changed")
    if contract["operations"]["createToken"]["successStatus"] != 201:
        fail("createToken returns 201 in this specification revision")
    if contract["operations"]["createToken"]["successSchema"] != "TokenPair":
        fail("createToken success schema changed")

    schemas = contract["schemas"]
    if set(schemas["PageOfCredential"]["properties"]) != {"elements", "pageMetadata"}:
        fail("PageOfCredential no longer matches the pinned specification")
    if set(schemas["PageMetadata"]["properties"]) != {
        "pageNumber",
        "pageSize",
        "totalElements",
        "totalPages",
    }:
        fail("PageMetadata no longer matches the pinned specification")
    if set(schemas["Credential"]["required"]) != {
        "accountType",
        "creationTimestamp",
        "credentialType",
        "id",
        "modificationTimestamp",
        "resource",
        "username",
    }:
        fail("Credential required fields no longer match the pinned specification")
    if set(schemas["AuthenticatedResource"]["required"]) != {
        "domainNames",
        "resourceId",
        "resourceName",
        "resourceType",
    }:
        fail("AuthenticatedResource required fields no longer match the pinned specification")

    expiration = schemas["ExpirationDetails"]["properties"]
    if set(expiration) != {"expiryDate", "lastCheckedDate", "connectivityStatus", "status"}:
        fail("ExpirationDetails no longer matches the pinned 9.0.0.0 specification")
    if NINE_ONE_ONLY_PROPERTY in expiration:
        fail(
            f"ExpirationDetails.{NINE_ONE_ONLY_PROPERTY} exists only in the 9.1.0.0 "
            "revision; the contract must come from 9.0.0.0"
        )

    if contract["resourceTypes"] != RESOURCE_TYPES:
        fail(f"resource types drifted from the 9.0.0.0 specification: {contract['resourceTypes']!r}")
    if contract["accountTypes"] != ACCOUNT_TYPES:
        fail("account types drifted from the 9.0.0.0 specification")

    pagination = contract["pagination"]
    if (pagination["pageNumberParameter"], pagination["pageSizeParameter"]) != (
        "pageNumber",
        "pageSize",
    ):
        fail("pagination parameter names changed")
    if pagination["firstPageNumber"] != 0:
        fail("pagination starts at page 0 in this specification revision")

    handshake = contract["sdkConnectionHandshake"]
    if (handshake["method"], handshake["path"]) != ("GET", HANDSHAKE_PATH):
        fail("the declared SDK connection handshake route changed")
    if handshake["operationId"] is not None:
        fail("the SDK handshake route carries no operationId in this specification revision")

    if sources["commitSha"] != SOURCE_SHA or sources["specPath"] != SOURCE_PATH:
        fail("official source provenance changed")
    if sources["tag"] != SOURCE_TAG or sources["specInfoVersion"] != API_VERSION:
        fail("official source revision changed")
    if sources["repositoryLicense"] != "Apache-2.0":
        fail("official source license changed")
    source_operations = {
        row["operationId"]: (row["method"], row["path"]) for row in sources["operations"]
    }
    if source_operations != EXPECTED_OPERATIONS:
        fail("official_sources.json must record every exact operationId used")
    for row in sources["operations"]:
        if row["commitSha"] != SOURCE_SHA or row["specPath"] != SOURCE_PATH:
            fail(f"operation {row['operationId']} is not pinned to the spec path and commit")
    if SOURCE_SHA not in sources["specUrl"] or SOURCE_PATH not in sources["specUrl"]:
        fail("the recorded spec URL is not commit-pinned")
    if sources["sdkPrerequisite"]["requiredVersion"] != SDK_VERSION:
        fail("the recorded SDK prerequisite version changed")
    if sources["sdkPrerequisite"]["vendoredBySeed"] is not False:
        fail("the SDK is an environment prerequisite and must not be vendored")


def verify_candidate_shape() -> None:
    module = (ROOT / "src/VcfSddcManager.CredentialInventory.psm1").read_text(encoding="utf-8")
    manifest = (ROOT / "src/VcfSddcManager.CredentialInventory.psd1").read_text(encoding="utf-8")

    if "Invoke-VcfGetCredentials" not in module:
        fail("implementation must page the collection through the SDK's Invoke-VcfGetCredentials")
    forbidden = [
        "Invoke-RestMethod",
        "Invoke-WebRequest",
        "System.Net.Http",
        "HttpClient",
        "WebClient",
        "curl ",
    ]
    used = [token for token in forbidden if token.lower() in module.lower()]
    if used:
        fail("direct HTTP clients are not allowed: " + ", ".join(used))
    if "Export-ModuleMember -Function Get-VcfSddcManagerCredentialInventory" not in module:
        fail("public function export changed")
    if SDK_MODULE not in manifest or SDK_VERSION not in manifest:
        fail("the module manifest must keep pinning the provided SDK prerequisite")


def read_log(log_path: Path):
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]


def verify_wire(records) -> None:
    if not records:
        fail("the loopback mock received no requests")

    allowed = set(EXPECTED_OPERATIONS.values()) | {("GET", HANDSHAKE_PATH)}
    for record in records:
        if (record["method"], record["path"]) not in allowed:
            fail(
                "request used a route outside docs/contract.json: "
                f"{record['method']} {record['path']}"
            )

    tokens = [r for r in records if r["path"] == "/v1/tokens"]
    if len(tokens) != 1:
        fail(f"expected exactly one createToken request, received {len(tokens)}")
    if tokens[0]["method"] != "POST":
        fail("createToken must be a POST")
    try:
        token_body = json.loads(tokens[0]["body"])
    except json.JSONDecodeError as error:
        fail(f"createToken body is not JSON: {error}")
    if set(token_body) - {"username", "password", "apiKey", "idToken"}:
        fail(f"createToken body carries fields outside TokenCreationSpec: {sorted(token_body)}")

    handshakes = [r for r in records if r["path"] == HANDSHAKE_PATH]
    if len(handshakes) != 1:
        fail(
            "the SDK connection handshake must happen exactly once, "
            f"received {len(handshakes)}"
        )

    gets = [r for r in records if r["path"] == "/v1/credentials"]
    if len(gets) != len(EXPECTED_QUERIES):
        fail(
            f"expected exactly {len(EXPECTED_QUERIES)} getCredentials requests "
            f"(every page of every collection, fetched once), received {len(gets)}"
        )

    for index, (record, expected_query) in enumerate(zip(gets, EXPECTED_QUERIES)):
        if record["method"] != "GET":
            fail(f"getCredentials request {index} must be a GET")
        if record["body"] != "":
            fail(f"getCredentials request {index} must not carry a request body")

        pairs = parse_query(record["query"])
        names = [name for name, _ in pairs]

        unknown = [name for name in names if name not in DECLARED_QUERY_PARAMETERS]
        if unknown:
            fail(
                f"getCredentials request {index} sent query parameters the pinned "
                f"specification does not declare: {unknown!r}"
            )
        if "resourceIp" in names:
            fail(
                f"getCredentials request {index} sent the deprecated resourceIp parameter"
            )
        for name, value in pairs:
            if name in OPTIONAL_FILTERS and not value:
                fail(
                    f"getCredentials request {index} sent optional filter {name!r} with an "
                    "empty value; an unset optional filter must be omitted from the query "
                    f"string entirely: {record['query']!r}"
                )
        for required in ("pageNumber", "pageSize"):
            if required not in names:
                fail(f"getCredentials request {index} omitted {required}")
        if len(names) != len(set(names)):
            fail(f"getCredentials request {index} repeated a query parameter")

        order = [DECLARED_QUERY_PARAMETERS.index(name) for name in names]
        if order != sorted(order):
            fail(
                f"getCredentials request {index} does not send query parameters in the "
                f"order the specification declares them: {record['query']!r}"
            )

        if record["query"] != expected_query:
            fail(
                f"getCredentials request {index} wire query is "
                f"{record['query']!r}, expected {expected_query!r}"
            )

        headers = record["headers"]
        if headers.get("authorization") != "Bearer loopback-access-token":
            fail(f"getCredentials request {index} did not use the SDK connection bearer token")
        if "application/json" not in str(headers.get("accept", "")).lower():
            fail(f"getCredentials request {index} does not accept application/json")

    ordered_paths = [(r["method"], r["path"]) for r in records]
    expected_paths = [("POST", "/v1/tokens"), ("GET", HANDSHAKE_PATH)] + [
        ("GET", "/v1/credentials")
    ] * len(EXPECTED_QUERIES)
    if ordered_paths != expected_paths:
        fail(f"the overall request sequence is wrong: {ordered_paths!r}")


def run_integration() -> None:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        fail("pwsh is required by this PowerShell task")

    with tempfile.TemporaryDirectory(prefix="vcf90-0003-") as temp_name:
        temp = Path(temp_name)
        log_path = temp / "requests.jsonl"
        port_path = temp / "port"
        output_path = temp / "result.json"

        environment = os.environ.copy()
        environment["NO_PROXY"] = "127.0.0.1,localhost"
        environment["no_proxy"] = "127.0.0.1,localhost"
        environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
        environment["POWERSHELL_UPDATECHECK"] = "Off"

        server = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(ROOT / "tests/mock_sddc_manager.py"),
                "--contract",
                str(ROOT / "docs/contract.json"),
                "--log",
                str(log_path),
                "--port-file",
                str(port_path),
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not port_path.exists() and time.monotonic() < deadline:
                if server.poll() is not None:
                    stdout, stderr = server.communicate(timeout=2)
                    fail(f"mock exited during startup\nstdout: {stdout}\nstderr: {stderr}")
                time.sleep(0.02)
            if not port_path.exists():
                fail("mock did not publish its loopback port")
            port = int(port_path.read_text(encoding="ascii"))

            completed = subprocess.run(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(ROOT / "tests/exercise.ps1"),
                    "-Port",
                    str(port),
                    "-OutputFile",
                    str(output_path),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if completed.returncode != 0:
                fail(
                    "PowerShell integration failed\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            if not output_path.exists():
                fail("PowerShell integration did not write a result")

            result = json.loads(output_path.read_text(encoding="utf-8-sig"))
            if result.get("elementTypeNames") != EXPECTED_RESULT["elementTypeNames"]:
                fail(
                    "the inventory must return the SDK's own Credential model objects, "
                    f"received {result.get('elementTypeNames')!r}"
                )
            scenarios = result.get("scenarios") or {}
            if list(scenarios) != list(EXPECTED_RESULT["scenarios"]):
                fail(f"scenario set changed: {list(scenarios)!r}")
            for name, expected in EXPECTED_RESULT["scenarios"].items():
                if scenarios[name] != expected:
                    fail(
                        f"scenario {name!r} returned the wrong collection or order.\n"
                        f"  expected: {expected!r}\n"
                        f"  actual:   {scenarios[name]!r}"
                    )
            for name in ("pageSize3", "pageSize9"):
                if scenarios[name] != scenarios["pageSize4"]:
                    fail(
                        f"scenario {name!r} does not agree with 'pageSize4'; the emitted "
                        "order must not depend on where the page boundaries fall"
                    )

            verify_wire(read_log(log_path))
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


def main() -> None:
    verify_contract()
    verify_candidate_shape()
    run_integration()
    print(
        "PASS: the credential inventory read every page of the 9.0.0.0 getCredentials "
        "collection, emitted it in a stable order, and sent only the pinned wire shape"
    )


if __name__ == "__main__":
    try:
        main()
    except (
        AssertionError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
