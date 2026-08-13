#!/usr/bin/env python3
"""Offline protected verification for the vcfcreds credential inventory CLI.

Starts the contract-pinned loopback SDDC Manager from tests/mock_sddc_manager.py,
drives `python3 -m vcfcreds` against it, and asserts both the emitted document
and the exact wire shape of every request that reached the server. No live
VMware endpoint is contacted.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_sddc_manager import (  # noqa: E402
    ACCESS_TOKEN,
    DATASETS,
    EXPECTED_OPERATION_IDS,
    VALID_PASSWORD,
    VALID_USERNAME,
    load_contract,
    start_contract_server,
)

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"

COMMIT_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
PRODUCT_VERSION = "9.0.0.0"

# The 9.0.0.0 revision of the specification documents exactly these seven
# resource types. The 9.1.0.0 revision of the same file adds HCX_MANAGER and
# VSP; a contract carrying those did not come from the pinned tag.
RESOURCE_TYPES_9_0 = [
    "ESXI", "VCENTER", "PSC", "NSXT_MANAGER", "NSXT_EDGE", "NSX_ALB", "BACKUP",
]
UNDOCUMENTED_RESOURCE_TYPES = ["HCX_MANAGER", "VSP", "NOT_A_VCF_RESOURCE"]

RECORD_FIELDS = {
    "id", "username", "accountType", "credentialType",
    "resourceId", "resourceName", "resourceType",
}


def expected_record(identifier: str, username: str, account_type: str,
                    credential_type: str, resource_id: str,
                    resource_name: str, resource_type: str) -> dict:
    """Spell the required projection independently of the mock response code."""
    return {
        "id": identifier,
        "username": username,
        "accountType": account_type,
        "credentialType": credential_type,
        "resourceId": resource_id,
        "resourceName": resource_name,
        "resourceType": resource_type,
    }


# These complete records are in the required resourceName/username/id order.
# Keeping expected values independent of DATASETS ensures the verifier checks
# the projection itself, not merely its keys and two of its values.
EXPECTED_PRIMARY = [
    expected_record(
        "1d47e8b3-0c95-42a6-8e74-59f1a2d3b806", "backup-admin", "SERVICE", "FTP",
        "7c25a4f8-16d3-4b09-9e58-3f0c6d81ba47", "backup-01.vcf.local", "BACKUP"),
    expected_record(
        "48ba0c93-6f17-4d85-b3ea-1c92f7580d36", "root", "SYSTEM", "SSH",
        "e0b73d19-8f42-4a56-91c7-24d5b6e08f31", "esxi-02.vcf.local", "ESXI"),
    expected_record(
        "9a06c2f5-4d81-4739-b2ce-6e85f03a17d9", "svc-vcf-esxi02", "SERVICE", "API",
        "e0b73d19-8f42-4a56-91c7-24d5b6e08f31", "esxi-02.vcf.local", "ESXI"),
    expected_record(
        "c21f7a86-5b39-4e02-97d4-8a60b3f1e547", "root", "USER", "SSH",
        "82d4e670-1c95-4f38-a6b2-70e91d5c83a4", "esxi-04.vcf.local", "ESXI"),
    expected_record(
        "b83c5a17-9e26-4f88-a30d-71c4e6b25f10", "root", "USER", "SSH",
        "d4e91b60-52a8-4c37-bf19-8a03d7e15c62", "esxi-11.vcf.local", "ESXI"),
    expected_record(
        "3e5b9d74-a218-4c60-8f93-0b7e1c4a2568", "admin", "USER", "API",
        "5f1a8c03-7e64-4d29-b085-9c37e2a416db", "nsxt-mgmt.vcf.local", "NSXT_MANAGER"),
    expected_record(
        "6f2d1e40-7a53-4d0b-9c11-2a8f5b31c904",
        "administrator@vsphere.local", "USER", "SSO",
        "a1c0f8d2-3b47-4e91-8f65-0d2e7c419a33", "vcenter-mgmt.vcf.local", "VCENTER"),
]
EXPECTED_PRIMARY_ESXI = EXPECTED_PRIMARY[1:5]
EXPECTED_ALIGNED = [
    expected_record(
        "0d29b7c4-1f68-4a03-95e7-8b4c6d10f2a5", "backup-admin", "SERVICE", "FTP",
        "6e83f5a0-2d47-4c19-b76a-95201f8de4c3", "backup-02.vcf.local", "BACKUP"),
    expected_record(
        "5b90d1e7-4c26-48f3-b105-9a7e34d06c82", "root", "USER", "SSH",
        "3a7f2e18-9c05-4b63-a48d-1e6082c5f73b", "esxi-21.vcf.local", "ESXI"),
    expected_record(
        "2f8a03d6-4e71-49bc-a5c8-6b230e94f17d", "root", "USER", "SSH",
        "17e5b93c-8046-4f2a-95d1-b3c78e604a29", "esxi-22.vcf.local", "ESXI"),
    expected_record(
        "f37c1b05-8d42-4e96-a071-53b8e2c4f9a6", "root", "USER", "SSH",
        "0c94a7e3-6b18-4d52-8f07-e13c5a9b2764", "esxi-23.vcf.local", "ESXI"),
    expected_record(
        "7c14e6a9-b503-4d27-8fa1-30e95b2c8746", "admin", "USER", "API",
        "94c60d7b-5a13-4e08-82f6-c7b491e3a0d5", "nsxt-wld01.vcf.local", "NSXT_MANAGER"),
    expected_record(
        "a6e48f21-3c07-4b95-8d1f-62a09e75c3b8",
        "administrator@vsphere.local", "USER", "SSO",
        "b158c04d-7e92-4a36-91f8-4d072b6ea5c1", "vcenter-wld01.vcf.local", "VCENTER"),
]
EXPECTED_TIES = [
    expected_record(
        "06a81e4d-2c75-49b3-90f6-1d8e37a5c4b2", "root", "SYSTEM", "SSH",
        "02d9c4a7-6e31-48b5-af20-9c7531e6d842", "esxi-tie.vcf.local", "ESXI"),
    expected_record(
        "f6b14728-9d30-4c5a-8e21-73a0b4d962cf", "root", "USER", "SSH",
        "f1c8437a-25d9-4b60-ae18-7c0392d54f6b", "esxi-tie.vcf.local", "ESXI"),
]

SECRETS = sorted(
    {item["password"] for dataset in DATASETS.values() for item in dataset}
    | {VALID_PASSWORD, ACCESS_TOKEN}
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# -- seed integrity --------------------------------------------------------


def assert_seed_contract() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    expected_operations = [
        {"operationId": "createToken", "method": "POST", "path": "/v1/tokens"},
        {"operationId": "getCredentials", "method": "GET", "path": "/v1/credentials"},
    ]

    require(contract["openapi"] == "3.0.1", "contract OpenAPI version changed")
    require(contract["api_version"] == PRODUCT_VERSION,
            "contract is not the 9.0.0.0 product version")
    require(contract["source"]["commit_sha"] == COMMIT_SHA,
            "contract is not pinned to the 9.0.0.0 tag commit")
    require(contract["source"]["tag"] == "9.0.0.0", "contract tag changed")
    require(contract["source"]["spec_path"] == SPEC_PATH, "contract spec path changed")
    require(sources["commit_sha"] == COMMIT_SHA, "official source commit changed")
    require(sources["tag"] == "9.0.0.0", "official source tag changed")
    require(sources["spec_path"] == SPEC_PATH, "official source spec path changed")
    require(sources["license"] == "Apache-2.0", "official source license changed")
    require(sources["product_version"] == PRODUCT_VERSION,
            "official source product version changed")
    require(COMMIT_SHA in sources["spec_url"],
            "official spec URL is not commit-pinned")
    require(SPEC_PATH in sources["spec_url"],
            "official spec URL does not point at the specification file")

    ids = [operation["operationId"] for operation in contract["operations"]]
    require(set(ids) == EXPECTED_OPERATION_IDS, "contract operationIds changed")
    require(ids == sources["operationIds"],
            "operationIds are not recorded in contract order")
    projection = [
        {key: operation[key] for key in ("operationId", "method", "path")}
        for operation in contract["operations"]
    ]
    require(projection == expected_operations,
            "contract methods, paths, or operationIds changed")
    require(sources["operations"] == expected_operations,
            "official sources do not record every exact operation")

    token_schema = contract["schemas"]["TokenCreationSpec"]
    require("required" not in token_schema,
            "the source declares every TokenCreationSpec property optional")
    require(set(token_schema["properties"]) == {"username", "password", "apiKey", "idToken"},
            "TokenCreationSpec properties changed")

    credentials = contract["operations"][1]
    parameters = {item["name"]: item for item in credentials["parameters"]}
    require(set(parameters) == {
        "resourceName", "resourceIp", "resourceType",
        "domainName", "pageNumber", "pageSize", "accountType",
    }, "getCredentials query parameters changed")
    require(all(item["required"] is False for item in parameters.values()),
            "getCredentials declares no required query parameter in 9.0.0.0")
    for name in ("pageNumber", "pageSize"):
        require(parameters[name]["schema"] == {"type": "string", "default": "0"},
                f"{name} is a string with default '0' in 9.0.0.0")
    require(parameters["resourceType"]["documentedValues"] == RESOURCE_TYPES_9_0,
            "resourceType values are not the 9.0.0.0 set; the 9.1.0.0 revision of "
            "this spec path adds HCX_MANAGER and VSP")
    require(set(contract["schemas"]["PageMetadata"]["properties"]) == {
        "pageNumber", "pageSize", "totalElements", "totalPages",
    }, "PageMetadata properties changed")
    require(set(contract["schemas"]["PageOfCredential"]["properties"]) == {
        "elements", "pageMetadata",
    }, "PageOfCredential properties changed")


# -- driving the client ----------------------------------------------------


def read_log(path: Path) -> list:
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return sorted(records, key=lambda item: item["seq"])


def run_client(scenario: str, page_size: int, resource_type: str | None = None):
    contract = load_contract(CONTRACT_PATH)
    with tempfile.TemporaryDirectory() as work:
        log_path = Path(work) / "requests.jsonl"
        log_path.touch()
        server = start_contract_server(contract, log_path, scenario)
        argv = [
            sys.executable, "-B", "-m", "vcfcreds",
            "--base-url", server.uri,
            "--username", VALID_USERNAME,
            "--password", VALID_PASSWORD,
            "--page-size", str(page_size),
        ]
        if resource_type is not None:
            argv += ["--resource-type", resource_type]
        client_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        # The acceptance service is always loopback. Ensure ambient proxy
        # configuration cannot redirect these requests outside the fixture.
        client_env["NO_PROXY"] = "127.0.0.1,localhost"
        client_env["no_proxy"] = "127.0.0.1,localhost"
        try:
            completed = subprocess.run(
                argv,
                cwd=ROOT,
                env=client_env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            server.shutdown()
            server.server_close()
        return completed, read_log(log_path)


def assert_no_secret_output(completed) -> None:
    output = completed.stdout + completed.stderr
    for secret in SECRETS:
        require(secret not in output, f"the CLI exposed the sensitive value {secret!r}")


# -- wire-shape assertions -------------------------------------------------


def assert_token_request(record) -> None:
    require(record["operationId"] == "createToken",
            f"expected createToken first, saw {record['operationId']!r}")
    require(record["method"] == "POST", "createToken must be a POST")
    require(record["path"] == "/v1/tokens", "createToken path changed")
    require(record["raw_query"] == "",
            f"createToken declares no query parameter, sent {record['raw_query']!r}")
    media_type = record["headers"].get("content-type", "").split(";")[0].strip()
    require(media_type == "application/json",
            f"createToken must send application/json, sent {media_type!r}")
    require("authorization" not in record["headers"],
            "createToken is unauthenticated and must not carry a bearer token")

    spec = json.loads(record["body"])
    require(set(spec) == {"username", "password"},
            "TokenCreationSpec must carry only the supplied username and password; "
            f"unset apiKey/idToken must be absent, saw keys {sorted(spec)}")
    require(spec["username"] == VALID_USERNAME, "username was not sent verbatim")
    require(spec["password"] == VALID_PASSWORD, "password was not sent verbatim")
    require(record["status"] == 201, f"createToken returned {record['status']}")


def assert_page_requests(records, page_size: int, expected_pages: int,
                         resource_type: str | None) -> None:
    require(len(records) == expected_pages,
            f"expected exactly {expected_pages} getCredentials request(s), "
            f"saw {len(records)}")
    expected_keys = {"pageNumber", "pageSize"}
    if resource_type is not None:
        expected_keys.add("resourceType")

    for index, record in enumerate(records):
        where = f"getCredentials request {index}"
        require(record["operationId"] == "getCredentials",
                f"{where}: unexpected operation {record['operationId']!r}")
        require(record["method"] == "GET", f"{where} must be a GET")
        require(record["path"] == "/v1/credentials",
                f"{where}: path is {record['path']!r}")
        require(record["headers"].get("authorization") == f"Bearer {ACCESS_TOKEN}",
                f"{where}: must carry the access token as 'Bearer <accessToken>'")
        require(record["body"] == "", f"{where}: a GET must not carry a body")
        require("content-type" not in record["headers"],
                f"{where}: a bodyless GET must not declare a Content-Type")

        pairs = [tuple(pair) for pair in record["query"]]
        names = [name for name, _ in pairs]
        require(len(names) == len(set(names)),
                f"{where}: repeated query parameter in {record['raw_query']!r}")
        require(set(names) == expected_keys,
                f"{where}: query keys are {sorted(names)}, expected "
                f"{sorted(expected_keys)}; parameters the caller did not supply "
                "must be omitted from the URL")
        for segment in record["raw_query"].split("&"):
            require(re.fullmatch(r"[^=&]+=[^=&]+", segment) is not None,
                    f"{where}: {segment!r} in {record['raw_query']!r} is not a "
                    "non-empty name=value pair")

        sent = dict(pairs)
        require(sent["pageSize"] == str(page_size),
                f"{where}: pageSize is {sent['pageSize']!r}, the caller asked for "
                f"{page_size}")
        require(sent["pageNumber"] == str(index),
                f"{where}: pageNumber is {sent['pageNumber']!r}, expected {index!r}; "
                "pages must be walked from 0 upward exactly once each")
        if resource_type is not None:
            require(sent["resourceType"] == resource_type,
                    f"{where}: resourceType is {sent['resourceType']!r}")
        require(record["status"] == 200, f"{where} returned {record['status']}")


def assert_emitted_document(completed, expected_records) -> None:
    require(completed.returncode == 0,
            "the CLI failed:\n" + completed.stdout[-1500:] + completed.stderr[-2500:])
    assert_no_secret_output(completed)
    require(completed.stderr == "",
            "a successful run must emit only its JSON document; stderr contained:\n"
            + completed.stderr[-2000:])

    require(completed.stdout.strip(), "the CLI emitted nothing on stdout")
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"stdout must carry the JSON document and nothing else ({error}):\n"
            f"{completed.stdout[:1500]}") from None
    require(set(document) == {"count", "credentials"},
            f"the emitted document keys are {sorted(document)}, expected "
            "['count', 'credentials']")
    emitted = document["credentials"]
    require(document["count"] == len(expected_records),
            f"count is {document['count']}, expected {len(expected_records)}")
    require(len(emitted) == len(expected_records),
            f"emitted {len(emitted)} credential(s), expected {len(expected_records)}; "
            "every page of the collection must be retrieved")

    for index, record in enumerate(emitted):
        require(set(record) == RECORD_FIELDS,
                f"credential {index} has fields {sorted(record)}, expected "
                f"{sorted(RECORD_FIELDS)}")
    actual_order = [
        (item["resourceName"], item["username"], item["id"])
        for item in emitted
    ]
    expected_order = [
        (item["resourceName"], item["username"], item["id"])
        for item in expected_records
    ]
    require(actual_order == expected_order,
            "credentials are not in the required stable order (resourceName, then "
            f"username, then id):\n  emitted  {actual_order}\n  expected {expected_order}")

    for index, (actual, expected) in enumerate(zip(emitted, expected_records)):
        require(actual == expected,
                f"credential {index} was not projected exactly from Credential and "
                f"its nested resource:\n  emitted  {actual}\n  expected {expected}")

    identifiers = [item["id"] for item in emitted]
    require(len(identifiers) == len(set(identifiers)),
            "the same credential was emitted more than once")


def assert_success(scenario: str, page_size: int, expected_pages: int,
                   expected_records, resource_type: str | None = None) -> None:
    completed, records = run_client(scenario, page_size, resource_type)
    assert_emitted_document(completed, expected_records)
    require(all(item["operationId"] is not None for item in records),
            "the CLI called a route the pinned contract does not name")
    require(records, "the CLI reached the server with no requests at all")
    assert_token_request(records[0])
    require(sum(item["operationId"] == "createToken" for item in records) == 1,
            "the access token must be created exactly once per run")
    assert_page_requests(records[1:], page_size, expected_pages, resource_type)


def assert_unknown_resource_type_is_not_sent(resource_type: str) -> None:
    completed, records = run_client("primary", 3, resource_type)
    require(completed.returncode != 0,
            f"the CLI accepted resourceType {resource_type!r}, which the 9.0.0.0 "
            "contract does not document")
    assert_no_secret_output(completed)
    require(records == [],
            f"the CLI contacted the server with an unsupported resourceType "
            f"{resource_type!r}; it must be rejected before any request is sent")


def assert_token_rejection_fails_loudly() -> None:
    completed, records = run_client("token-rejected", 3)
    require(completed.returncode != 0,
            "the CLI reported success after createToken was rejected")
    assert_no_secret_output(completed)
    require([item["operationId"] for item in records] == ["createToken"],
            "the CLI must stop once the token request fails, saw "
            f"{[item['operationId'] for item in records]}")
    require("401" in completed.stdout + completed.stderr,
            "the CLI did not report the 401 from createToken")


def assert_credentials_rejection_fails_loudly() -> None:
    completed, records = run_client("credentials-rejected", 3)
    require(completed.returncode != 0,
            "the CLI reported success after getCredentials failed")
    assert_no_secret_output(completed)
    require([item["operationId"] for item in records] ==
            ["createToken", "getCredentials"],
            "the CLI must stop after the failed credential page, saw "
            f"{[item['operationId'] for item in records]}")
    require("500" in completed.stdout + completed.stderr,
            "the CLI did not report the 500 from getCredentials")


def main() -> int:
    assert_seed_contract()

    # Seven credentials over a page size of three: three pages, last one short.
    assert_success("primary", 3, 3, EXPECTED_PRIMARY)
    # One page that holds everything: the walk must stop immediately.
    assert_success("primary", 10, 1, EXPECTED_PRIMARY)
    # An optional filter the caller did supply travels; the rest stay omitted.
    assert_success("primary", 3, 2, EXPECTED_PRIMARY_ESXI, resource_type="ESXI")
    # Six credentials over a page size of three divides evenly, so the final page
    # is full: stopping on a short page would fetch one page too many.
    assert_success("aligned", 3, 2, EXPECTED_ALIGNED)
    # Same resourceName and username, reverse id arrival: exercise the tertiary
    # sort key independently of field projection.
    assert_success("ties", 1, 2, EXPECTED_TIES)
    # An empty collection omits `elements` entirely and reports zero pages.
    assert_success("empty", 3, 1, [])

    for resource_type in UNDOCUMENTED_RESOURCE_TYPES:
        assert_unknown_resource_type_is_not_sent(resource_type)
    assert_token_rejection_fails_loudly()
    assert_credentials_rejection_fails_loudly()

    print("PASS: the credential collection was paged completely, ordered stably, "
          "and sent exactly the wire shape the 9.0.0.0 contract defines")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, ValueError, OSError,
            subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
