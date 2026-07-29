#!/usr/bin/env python3
"""Protected deterministic verification for the Java NSX Policy client."""

from __future__ import annotations

import base64
import json
import os
import select
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
CLIENT_PATH = ROOT / "NsxPolicyClient.java"
TEST_MAIN_PATH = ROOT / "TestMain.java"
MOCK_PATH = ROOT / "tools" / "mock_nsx_policy.py"
EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
EXPECTED_OPERATION_IDS = [
    "PatchGroupForDomain",
    "PatchSecurityPolicyForDomain",
]
EXPECTED_GROUP_BODY = (
    b'{"resource_type":"Group","display_name":"Source \\"blue\\"",'
    b'"expression":[{"resource_type":"IPAddressExpression",'
    b'"ip_addresses":["10.20.0.0/24","2001:db8::/64"]}]}'
)
EXPECTED_POLICY_BODY = (
    b'{"resource_type":"SecurityPolicy","display_name":"Application policy",'
    b'"category":"Application","sequence_number":120,"stateful":true,"rules":['
    b'{"resource_type":"Rule","display_name":"Allow app\\ntraffic",'
    b'"sequence_number":10,'
    b'"source_groups":["/infra/domains/prod east/groups/source+blue"],'
    b'"destination_groups":["/infra/domains/default/groups/destination"],'
    b'"services":["ANY"],"scope":["ANY"],"action":"ALLOW",'
    b'"direction":"IN_OUT"}]}'
)


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_and_check_contract() -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    operation_ids = [item["operationId"] for item in contract["operations"]]
    source_ids = [item["operationId"] for item in sources["operations"]]

    require(contract["swagger"] == "2.0", "contract is not OpenAPI 2.0")
    require(contract["info"]["version"] == "9.1.0.0", "wrong API version")
    require(contract["basePath"] == "/policy/api/v1", "wrong basePath")
    require(
        contract["derived_from"]["repository_commit_sha"] == EXPECTED_COMMIT,
        "contract commit is not pinned",
    )
    require(
        contract["derived_from"]["spec_path"] == EXPECTED_SPEC_PATH,
        "contract spec path is wrong",
    )
    require(operation_ids == EXPECTED_OPERATION_IDS, "wrong contract operations")
    require(source_ids == EXPECTED_OPERATION_IDS, "wrong provenance operations")
    require(
        sources["repository_commit_sha"] == EXPECTED_COMMIT,
        "provenance commit is not pinned",
    )
    require(
        sources["spec_path"] == EXPECTED_SPEC_PATH,
        "provenance spec path is wrong",
    )
    for operation in sources["operations"]:
        require(
            operation["repository_commit_sha"] == EXPECTED_COMMIT,
            f"{operation['operationId']} does not record the pinned commit",
        )
        require(
            operation["spec_path"] == EXPECTED_SPEC_PATH,
            f"{operation['operationId']} does not record the spec path",
        )
    return contract


def read_ready_line(process: subprocess.Popen[str]) -> dict[str, Any]:
    ready, _, _ = select.select([process.stdout], [], [], 5.0)
    require(bool(ready), "mock did not publish its loopback port")
    assert process.stdout is not None
    line = process.stdout.readline()
    require(bool(line), "mock exited before publishing its loopback port")
    return json.loads(line)


def one_header(request: dict[str, Any], name: str) -> str:
    values = request["headers"].get(name.lower())
    require(
        isinstance(values, list) and len(values) == 1,
        f"{name} must appear exactly once",
    )
    return values[0]


def request_body(request: dict[str, Any]) -> bytes:
    return base64.b64decode(request["body_base64"], validate=True)


def verify_wire(log_path: Path, contract: dict[str, Any]) -> None:
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    require(len(entries) == 2, f"expected exactly 2 requests, got {len(entries)}")

    expected = [
        (
            contract["operations"][0]["operationId"],
            "PATCH",
            "/policy/api/v1/infra/domains/prod%20east/"
            "groups/source%2Bblue",
            EXPECTED_GROUP_BODY,
        ),
        (
            contract["operations"][1]["operationId"],
            "PATCH",
            "/policy/api/v1/infra/domains/prod%20east/"
            "security-policies/allow%2Fedge",
            EXPECTED_POLICY_BODY,
        ),
    ]
    expected_auth = "Basic " + base64.b64encode(
        b"contract-user:s3cret-value"
    ).decode("ascii")

    for index, (request, wanted) in enumerate(zip(entries, expected), start=1):
        operation_id, method, raw_target, body = wanted
        require(request["sequence"] == index, "request sequence is unstable")
        require(
            request["operationId"] == operation_id,
            f"request {index} reached the wrong operation",
        )
        require(request["method"] == method, f"request {index} method is wrong")
        require(
            request["raw_target"] == raw_target,
            f"request {index} raw target is wrong: {request['raw_target']!r}",
        )
        require(
            one_header(request, "Authorization") == expected_auth,
            f"request {index} authentication is wrong",
        )
        require(
            one_header(request, "Accept") == "application/json",
            f"request {index} Accept is wrong",
        )
        require(
            one_header(request, "Content-Type") == "application/json",
            f"request {index} Content-Type is wrong",
        )
        actual_body = request_body(request)
        require(
            actual_body == body,
            f"request {index} body bytes are wrong:\n"
            + actual_body.decode("utf-8", errors="replace"),
        )
        require(
            int(one_header(request, "Content-Length")) == len(body),
            f"request {index} Content-Length is wrong",
        )

    group = json.loads(request_body(entries[0]))
    policy = json.loads(request_body(entries[1]))
    require(
        list(group) == ["resource_type", "display_name", "expression"],
        "unset Group properties were not omitted",
    )
    require(
        list(group["expression"][0]) == ["resource_type", "ip_addresses"],
        "unset IPAddressExpression properties were not omitted",
    )
    require(
        list(policy)
        == [
            "resource_type",
            "display_name",
            "category",
            "sequence_number",
            "stateful",
            "rules",
        ],
        "unset SecurityPolicy properties were not omitted",
    )
    require(
        list(policy["rules"][0])
        == [
            "resource_type",
            "display_name",
            "sequence_number",
            "source_groups",
            "destination_groups",
            "services",
            "scope",
            "action",
            "direction",
        ],
        "unset Rule properties were not omitted",
    )

    forbidden = {
        "description",
        "notes",
        "tags",
        "id",
        "_revision",
        "_create_time",
        "_create_user",
        "_last_modified_time",
        "_last_modified_user",
        "_protection",
        "_system_owned",
        "marked_for_delete",
        "path",
        "relative_path",
        "unique_id",
        "destinations_excluded",
        "disabled",
        "logged",
        "sources_excluded",
    }

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            require(not (set(value) & forbidden), "body contains an unset field")
            for nested in value.values():
                inspect(nested)
        elif isinstance(value, list):
            for nested in value:
                inspect(nested)

    inspect(group)
    inspect(policy)


def main() -> int:
    try:
        contract = load_and_check_contract()
        with tempfile.TemporaryDirectory(prefix="vcf91-0083-") as temp_text:
            temp = Path(temp_text)
            classes = temp / "classes"
            classes.mkdir()
            log_path = temp / "requests.jsonl"
            report_path = temp / "reports" / "change.json"

            compiled = subprocess.run(
                [
                    "javac",
                    "--release",
                    "17",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    os.fspath(classes),
                    os.fspath(CLIENT_PATH),
                    os.fspath(TEST_MAIN_PATH),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=15,
            )
            require(
                compiled.returncode == 0,
                "javac failed:\n" + compiled.stdout + compiled.stderr,
            )

            mock = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    os.fspath(MOCK_PATH),
                    "--contract",
                    os.fspath(CONTRACT_PATH),
                    "--log",
                    os.fspath(log_path),
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                ready = read_ready_line(mock)
                port = ready["port"]
                require(
                    isinstance(port, int) and 0 < port < 65536,
                    "mock published an invalid port",
                )
                run = subprocess.run(
                    [
                        "java",
                        "-cp",
                        os.fspath(classes),
                        "TestMain",
                        f"http://127.0.0.1:{port}",
                        os.fspath(report_path),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                require(
                    run.returncode == 0,
                    "TestMain failed:\n" + run.stdout + run.stderr,
                )
                require(
                    run.stdout.strip() == "ALL NSX POLICY CONTRACT CHECKS PASSED",
                    "TestMain did not emit its success marker",
                )
                verify_wire(log_path, contract)
            finally:
                mock.terminate()
                try:
                    mock.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    mock.kill()
                    mock.wait(timeout=3)

        print("PASS: exact NSX Policy contract, omission, and partial report verified")
        return 0
    except (VerificationError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as error:
        print(f"FAIL: timed out: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
