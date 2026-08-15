#!/usr/bin/env python3
"""Deterministic verifier for the single-file Java VCF Automation client."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

sys.dont_write_bytecode = True

from mock_vcfa import ContractMock


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SOURCE = (
    "https://developer.broadcom.com/xapis/vm-apps-org-policies/9.0/"
    "deployment/api/deployments/deploymentId/patch/"
)
EXPECTED_REQUESTS = [
    {
        "deploymentId": "dep 9/blue",
        "body": {
            "description": 'Quarterly "blue" refresh\nowner: café \\ core\u0001',
        },
    },
    {
        "deploymentId": "dep 9/blue",
        "body": {
            "description": 'Quarterly "blue" refresh\nowner: café \\ core\u0001',
        },
    },
    {
        "deploymentId": "all?#/% café",
        "body": {
            "description": "",
            "iconId": 'icon-"quoted"-\\-\b-end',
            "name": "Release\t雪\rline",
        },
    },
    {
        "deploymentId": "unset",
        "body": {},
    },
]
PATH_PREFIX = "/deployment/api/deployments/"


class JsonObject(list[tuple[str, object]]):
    """Preserve object member pairs so duplicate JSON members cannot be hidden."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_documentation() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    source = contract["source"]
    require(source["kind"] == "reference-documentation", "contract source kind changed")
    require(
        "reference documentation rather than a published specification" in source["statement"],
        "contract must plainly distinguish the xAPI reference from a published specification",
    )
    require(contract["productVersion"] == "9.0", "contract is not pinned to VCF 9.0")

    operations = contract["operations"]
    require(len(operations) == 1, "mock contract must name exactly one operation")
    operation = operations[0]
    require(operation["operationId"] == "patchDeployment", "unexpected operation ID")
    require(operation["method"] == "PATCH", "unexpected HTTP method")
    require(
        operation["pathTemplate"] == "/deployment/api/deployments/{deploymentId}",
        "unexpected operation path",
    )
    require(
        operation["authentication"] == {"type": "http", "scheme": "bearer"},
        "unexpected authentication contract",
    )
    request = operation["request"]
    require(request["required"] is True, "request body must be required")
    require(request["contentType"] == "application/json", "unexpected request media type")
    members = request["body"]["members"]
    require(list(members) == ["description", "iconId", "name"], "request members changed")
    require(
        all(member["required"] is False for member in members.values()),
        "all DeploymentUpdate members must remain optional",
    )
    require(
        set(operation["responses"]) == {"200", "401", "403", "404"},
        "documented response set changed",
    )

    manifest = json.loads(
        (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
    )
    require(len(manifest["sources"]) == 1, "source manifest must contain the page used")
    official = manifest["sources"][0]
    require(official["url"] == EXPECTED_SOURCE, "source must be the versioned Broadcom xAPI page")
    require(official["fetchedOn"] == "2026-08-13", "source fetch date changed")
    require(
        official["operation"]
        == {
            "name": "Patch Deployment",
            "method": "PATCH",
            "path": "/deployment/api/deployments/{deploymentId}",
        },
        "source manifest does not identify the documented operation",
    )


def decode_body(raw_body: bytes, request_number: int) -> dict[str, object]:
    try:
        text = raw_body.decode("utf-8", errors="strict")
        pairs = json.loads(text, object_pairs_hook=JsonObject)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssertionError(f"request {request_number}: body is not UTF-8 JSON: {error}") from error
    require(
        isinstance(pairs, JsonObject),
        f"request {request_number}: top-level JSON value is not an object",
    )
    keys = [key for key, _value in pairs]
    require(
        len(keys) == len(set(keys)),
        f"request {request_number}: JSON contains duplicate members",
    )
    return dict(pairs)


def verify_target(target: str, deployment_id: str, request_number: int) -> None:
    require("?" not in target, f"request {request_number}: unexpected query string")
    require(target.startswith(PATH_PREFIX), f"request {request_number}: wrong operation path")
    segment = target[len(PATH_PREFIX) :]
    require(segment != "", f"request {request_number}: deployment ID segment is empty")
    require("/" not in segment, f"request {request_number}: deployment ID spans path segments")
    try:
        decoded = unquote(segment, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise AssertionError(
            f"request {request_number}: deployment ID is not valid UTF-8"
        ) from error
    require(decoded == deployment_id, f"request {request_number}: wrong deployment ID segment")


def verify_client() -> None:
    with tempfile.TemporaryDirectory(prefix="vcfa-java-") as temp_dir:
        classes = Path(temp_dir) / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-d",
                str(classes),
                str(ROOT / "src" / "VcfAutomationClient.java"),
                str(ROOT / "TestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        require(
            compile_result.returncode == 0,
            "Java compilation failed:\n" + compile_result.stdout + compile_result.stderr,
        )

        with ContractMock(ROOT / "docs" / "contract.json") as mock:
            run_result = subprocess.run(
                ["java", "-cp", str(classes), "TestMain", mock.base_uri, "verifier-token"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            require(
                run_result.returncode == 0,
                "TestMain failed:\n" + run_result.stdout + run_result.stderr,
            )
            requests = mock.request_log()
            effect_count = mock.effect_count

    require(
        len(requests) == len(EXPECTED_REQUESTS),
        f"expected {len(EXPECTED_REQUESTS)} HTTP requests, got {len(requests)}",
    )
    for index, (request, expected) in enumerate(
        zip(requests, EXPECTED_REQUESTS, strict=True), start=1
    ):
        require(request["method"] == "PATCH", f"request {index}: method is not PATCH")
        verify_target(request["target"], expected["deploymentId"], index)
        require(
            request["deploymentId"] == expected["deploymentId"],
            f"request {index}: mock decoded the wrong deployment ID",
        )
        headers = request["headers"]
        require(
            headers.get("authorization") == ["Bearer verifier-token"],
            f"request {index}: wrong Authorization header",
        )
        require(
            headers.get("content-type") == ["application/json"],
            f"request {index}: wrong Content-Type header",
        )
        require(
            headers.get("accept") == ["application/json"],
            f"request {index}: wrong Accept header",
        )
        parsed = decode_body(request["body"], index)
        require(parsed == expected["body"], f"request {index}: wrong JSON members or values")

    require(requests[0]["body"] == requests[1]["body"], "repeated call body was not stable")
    require(requests[0]["target"] == requests[1]["target"], "repeated call target was not stable")
    require(
        requests[0]["headers"] == requests[1]["headers"],
        "repeated call generated different HTTP headers",
    )
    require(effect_count == 2, "repeating the replacement PATCH duplicated its effect")


def main() -> int:
    try:
        verify_documentation()
        verify_client()
    except (AssertionError, KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF Automation PATCH request matches the pinned 9.0 contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
