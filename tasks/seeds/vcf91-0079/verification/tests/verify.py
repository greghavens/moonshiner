#!/usr/bin/env python3
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
MOCK = ROOT / "tests" / "mock_nsx.py"
CLIENT = ROOT / "NsxPolicyClient.java"
TEST_MAIN = ROOT / "tests" / "TestMain.java"


def fail(message):
    print(f"VERIFY_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate_contract():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(
        (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
    )
    operations = {entry["operationId"]: entry for entry in contract["operations"]}
    if set(operations) != {"PatchSegment", "ReadIntentStatus"}:
        fail("contract operationIds changed")
    if sources["operationIds"] != ["PatchSegment", "ReadIntentStatus"]:
        fail("official source operationIds changed")
    if sources["repository_commit_sha"] != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26":
        fail("official source commit changed")
    if sources["spec_path"] != "specifications/nsx/openapi-2.0/nsx_policy_api.yaml":
        fail("official source path changed")
    if operations["PatchSegment"]["method"] != "PATCH":
        fail("PatchSegment method changed")
    if operations["ReadIntentStatus"]["method"] != "GET":
        fail("ReadIntentStatus method changed")
    return contract


def read_requests(log_path):
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def verify_wire_shape(contract, requests):
    if len(requests) != 6:
        fail(f"expected two PATCH requests and four polls, got {len(requests)} requests")
    if [item["operationId"] for item in requests] != [
        "PatchSegment",
        "ReadIntentStatus",
        "ReadIntentStatus",
        "ReadIntentStatus",
        "PatchSegment",
        "ReadIntentStatus",
    ]:
        fail("operation order does not PATCH and poll each intent to terminal state")

    base_path = contract["base_path"]
    tier_id = "tier 1"
    segment_id = "orders/blue"
    encoded_tier = urllib.parse.quote(tier_id, safe="")
    encoded_segment = urllib.parse.quote(segment_id, safe="")
    expected_patch_target = (
        f"{base_path}/infra/tier-1s/{encoded_tier}/segments/{encoded_segment}"
    )
    intent_path = f"/infra/tier-1s/{tier_id}/segments/{segment_id}"
    expected_status_target = (
        f"{base_path}/infra/realized-state/status?"
        + urllib.parse.urlencode({"intent_path": intent_path})
    )

    patch = requests[0]
    if patch["method"] != "PATCH" or patch["target"] != expected_patch_target:
        fail(f"wrong PatchSegment request target: {patch['method']} {patch['target']}")

    expected_body = {
        "resource_type": "Segment",
        "display_name": 'Orders "blue" \\ primary',
        "subnets": [{"gateway_address": "10.42.0.1/24"}],
    }
    if patch.get("body_json") != expected_body:
        fail(f"PATCH JSON is not the exact minimal Segment shape: {patch.get('body')}")

    forbidden_body_fields = {
        "description",
        "dhcp_config_path",
        "admin_state",
        "tags",
        "transport_zone_path",
        "vlan_ids",
        "children",
    }
    sent_forbidden = forbidden_body_fields.intersection(patch["body_json"])
    if sent_forbidden:
        fail(f"unset optional Segment fields were serialized: {sorted(sent_forbidden)}")
    subnet = patch["body_json"]["subnets"][0]
    if set(subnet) != {"gateway_address"}:
        fail("unset optional SegmentSubnet fields were serialized")

    expected_auth = "Basic " + base64.b64encode(
        b"api-user:p@ss:word"
    ).decode("ascii")
    if patch["headers"].get("authorization") != expected_auth:
        fail("missing or incorrect HTTP Basic Authorization header")
    if patch["headers"].get("accept") != "application/json":
        fail("PATCH Accept header must be application/json")
    content_type = patch["headers"].get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        fail("PATCH Content-Type must be application/json")

    for index, poll in enumerate(requests[1:4], start=1):
        if poll["method"] != "GET" or poll["target"] != expected_status_target:
            fail(f"poll {index} has wrong ReadIntentStatus target: {poll['target']}")
        if poll["body"] != "":
            fail(f"poll {index} unexpectedly sent a request body")
        if poll["headers"].get("authorization") != expected_auth:
            fail(f"poll {index} is missing HTTP Basic authentication")
        if poll["headers"].get("accept") != "application/json":
            fail(f"poll {index} Accept header must be application/json")

        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(poll["target"]).query,
            keep_blank_values=True,
        )
        if query != {"intent_path": [intent_path]}:
            fail(f"poll {index} sent optional or malformed query parameters: {query}")
        for optional_name in ("include_enforced_status", "site_path"):
            if optional_name in query:
                fail(f"unset optional query field {optional_name} was serialized")

    second_tier = "tier/qa"
    second_segment = "billing green"
    second_patch_target = (
        f"{base_path}/infra/tier-1s/"
        f"{urllib.parse.quote(second_tier, safe='')}/segments/"
        f"{urllib.parse.quote(second_segment, safe='')}"
    )
    second_intent_path = (
        f"/infra/tier-1s/{second_tier}/segments/{second_segment}"
    )
    second_status_target = (
        f"{base_path}/infra/realized-state/status?"
        + urllib.parse.urlencode({"intent_path": second_intent_path})
    )
    second_patch = requests[4]
    if (
        second_patch["method"] != "PATCH"
        or second_patch["target"] != second_patch_target
    ):
        fail(
            "wrong second PatchSegment request target: "
            f"{second_patch['method']} {second_patch['target']}"
        )
    expected_second_body = {
        "resource_type": "Segment",
        "display_name": "Billing\nGreen",
        "subnets": [{"gateway_address": "10.43.0.1/24"}],
        "description": 'Temporary "QA" segment',
        "dhcp_config_path": "/infra/dhcp-server-configs/shared",
    }
    if second_patch.get("body_json") != expected_second_body:
        fail(
            "PATCH did not preserve explicitly set optional Segment fields: "
            + second_patch.get("body", "")
        )
    if second_patch["headers"].get("authorization") != expected_auth:
        fail("second PATCH is missing HTTP Basic authentication")
    if second_patch["headers"].get("accept") != "application/json":
        fail("second PATCH Accept header must be application/json")

    second_poll = requests[5]
    if (
        second_poll["method"] != "GET"
        or second_poll["target"] != second_status_target
    ):
        fail(f"second segment poll has wrong target: {second_poll['target']}")
    second_query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(second_poll["target"]).query,
        keep_blank_values=True,
    )
    if second_query != {"intent_path": [second_intent_path]}:
        fail(f"second segment poll query is malformed: {second_query}")
    if second_poll["body"] != "":
        fail("second segment poll unexpectedly sent a request body")
    if second_poll["headers"].get("authorization") != expected_auth:
        fail("second segment poll is missing HTTP Basic authentication")
    if second_poll["headers"].get("accept") != "application/json":
        fail("second segment poll Accept header must be application/json")


def main():
    contract = validate_contract()
    if shutil.which("javac") is None or shutil.which("java") is None:
        fail("Java compiler/runtime not found")

    with tempfile.TemporaryDirectory(prefix="vcf91-0079-") as temp_name:
        temp = Path(temp_name)
        classes = temp / "classes"
        classes.mkdir()
        request_log = temp / "requests.jsonl"
        request_log.touch()

        mock = subprocess.Popen(
            [
                sys.executable,
                str(MOCK),
                "--contract",
                str(CONTRACT),
                "--log",
                str(request_log),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            port_line = mock.stdout.readline().strip()
            if not port_line.isdigit():
                stderr = mock.stderr.read()
                fail(f"mock did not start: {port_line} {stderr}")
            base_uri = f"http://127.0.0.1:{port_line}"

            compile_result = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(classes),
                    str(CLIENT),
                    str(TEST_MAIN),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if compile_result.returncode != 0:
                fail("javac failed:\n" + compile_result.stdout + compile_result.stderr)

            run_result = subprocess.run(
                ["java", "-cp", str(classes), "TestMain", base_uri],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if run_result.returncode != 0:
                fail(
                    "TestMain failed:\n"
                    + run_result.stdout
                    + run_result.stderr
                )
            if run_result.stdout.strip() != "TEST_MAIN_OK":
                fail(f"unexpected TestMain output: {run_result.stdout!r}")

            verify_wire_shape(contract, read_requests(request_log))
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=3)

    print("VERIFY_OK: PatchSegment wire contract and terminal-state polling passed")


if __name__ == "__main__":
    main()
