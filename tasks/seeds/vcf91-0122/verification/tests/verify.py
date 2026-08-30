#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0122."""

from __future__ import annotations

import base64
import json
import secrets
import select
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
CLIENT = ROOT / "VcenterLibraryClient.java"
TEST_MAIN = ROOT / "tests" / "TestMain.java"
MOCK = ROOT / "tests" / "mock_vcenter.py"

EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
EXPECTED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
EXPECTED_OPERATION = "Content.LocalLibrary_create"
EXPECTED_POINTER = "#/paths/~1content~1local-library/post"
FORBIDDEN_MODEL_FIELDS = {
    "id",
    "creation_time",
    "last_modified_time",
    "last_sync_time",
    "type",
    "optimization_info",
    "version",
    "publish_info",
    "subscription_info",
    "server_guid",
    "security_policy_id",
    "unset_security_policy_id",
    "state_info",
    "configuration_info",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def check_contract() -> dict[str, Any]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    source = contract["source"]

    for actual in (
        source["repository_commit_sha"],
        sources["repository_commit_sha"],
    ):
        if actual != EXPECTED_COMMIT:
            fail("the specification commit is not pinned")
    for actual in (source["spec_path"], sources["spec_path"]):
        if actual != EXPECTED_SPEC:
            fail("the specification path changed")
    for actual in (source["spec_blob_sha"], sources["spec_blob_sha"]):
        if actual != EXPECTED_BLOB:
            fail("the specification blob is not pinned")
    if source["license"] != "Apache-2.0" or sources["license"] != "Apache-2.0":
        fail("the source license changed")
    if (
        contract["openapi"] != "3.0.3"
        or contract["info"] != {
            "title": "vSphere Automation API",
            "version": "9.1.0.0",
        }
        or contract["base_path"] != "/api"
    ):
        fail("the protected OpenAPI identity changed")
    if contract["security_schemes"] != {
        "api_key_auth": {
            "type": "apiKey",
            "name": "vmware-api-session-id",
            "in": "header",
        }
    }:
        fail("the protected security projection changed")

    operations = contract["operations"]
    if len(operations) != 1:
        fail("the contract must contain exactly one operation")
    operation = operations[0]
    if (
        operation["operationId"] != EXPECTED_OPERATION
        or operation["method"] != "POST"
        or operation["path"] != "/content/local-library"
    ):
        fail("the protected operation changed")
    if operation["parameters"] != [
        {
            "name": "Client-Token",
            "in": "header",
            "required": False,
            "schema": {"type": "string"},
            "description": (
                "A unique token generated on the client for each creation "
                "request. The token should be a universally unique identifier "
                "(UUID). This token can be used to guarantee idempotent "
                "creation. If not specified creation is not idempotent."
            ),
        }
    ]:
        fail("the Client-Token projection changed")
    if (
        operation["requestBody"]["required"] is not True
        or operation["requestBody"]["content"]["application/json"][
            "schema_ref"
        ]
        != "#/components/schemas/Content.LibraryModel"
        or set(operation["responses"]) != {"201", "400"}
        or operation["responses"]["201"]["schema"] != {"type": "string"}
        or operation["security"] != [{"api_key_auth": []}]
    ):
        fail("the operation request, response, or security contract changed")

    schemas = contract["schemas"]
    model_properties = schemas["Content.LibraryModel"]["properties"]
    backing_properties = schemas["Content.Library.StorageBacking"]["properties"]
    if not {
        "name",
        "storage_backings",
        "description",
        *FORBIDDEN_MODEL_FIELDS,
    }.issubset(model_properties):
        fail("the projected library model is incomplete")
    if set(backing_properties) != {"type", "datastore_id", "storage_uri"}:
        fail("the storage-backing projection changed")
    if schemas["Content.Library.StorageBacking.Type"] != {
        "type": "string",
        "enum": ["DATASTORE", "OTHER"],
    }:
        fail("the backing type projection changed")

    source_operations = sources["operations"]
    if len(source_operations) != 1:
        fail("official_sources must record exactly one operationId")
    recorded = source_operations[0]
    if (
        recorded["operationId"] != EXPECTED_OPERATION
        or recorded["method"] != "POST"
        or recorded["path"] != "/content/local-library"
        or recorded["yaml_pointer"] != EXPECTED_POINTER
        or recorded["repository_commit_sha"] != EXPECTED_COMMIT
        or recorded["spec_path"] != EXPECTED_SPEC
    ):
        fail("the official operation source changed")
    for projection in sources["schema_projections"]:
        if (
            projection["repository_commit_sha"] != EXPECTED_COMMIT
            or projection["spec_path"] != EXPECTED_SPEC
            or not projection["yaml_pointer"].startswith(
                "#/components/schemas/"
            )
        ):
            fail("a schema projection is not pinned to the YAML source")
    if "OpenAPI 3.0.3 YAML" not in sources["derivation"]:
        fail("official_sources no longer records YAML derivation")
    return contract


def read_banner(process: subprocess.Popen[str]) -> dict[str, Any]:
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], 5.0)
    if not ready:
        stderr = process.stderr.read() if process.stderr else ""
        fail("loopback fixture did not start: " + stderr)
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr else ""
        fail("loopback fixture exited during startup: " + stderr)
    return json.loads(line)


def expected_body(
    mode: str,
    name: str,
    backing_value: str,
) -> bytes:
    if mode == "other_empty":
        value: dict[str, Any] = {
            "name": name,
            "storage_backings": [
                {"type": "OTHER", "storage_uri": backing_value}
            ],
            "description": "",
        }
    else:
        value = {
            "name": name,
            "storage_backings": [
                {"type": "DATASTORE", "datastore_id": backing_value}
            ],
        }
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def check_log(
    log_path: Path,
    mode: str,
    session_id: str,
    client_token: str,
    body: bytes,
) -> None:
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    expected_requests = 2 if mode in {
        "drop_after_commit",
        "drop_every",
    } else 1
    expected_effects = 0 if mode == "http_503" else 1
    if len(events) != expected_requests:
        fail(f"{mode}: expected {expected_requests} requests")

    for index, event in enumerate(events):
        if event["seq"] != index + 1:
            fail(f"{mode}: request sequence is not contiguous")
        if (
            event["operation_id"] != EXPECTED_OPERATION
            or event["method"] != "POST"
            or event["raw_target"] != "/api/content/local-library"
        ):
            fail(f"{mode}: operation, method, or raw target mismatch")
        if (
            event["session_count"] != 1
            or event["session"] != session_id
            or event["client_token_count"] != 1
            or event["client_token"] != client_token
            or event["accept_count"] != 1
            or event["accept"] != "application/json"
            or event["content_type_count"] != 1
            or event["content_type"] != "application/json"
            or event["content_length_count"] != 1
            or event["content_length"] != str(len(body))
            or event["transfer_encoding_count"] != 0
            or event["authorization_count"] != 0
        ):
            fail(f"{mode}: exact request header shape mismatch")
        decoded = base64.b64decode(event["body_b64"], validate=True)
        if event["body_length"] != len(body) or decoded != body:
            fail(f"{mode}: exact compact UTF-8 request body mismatch")
        if event["effect_count"] != expected_effects:
            fail(f"{mode}: unexpected number of mutation effects")
        if event["new_effect"] is not (
            expected_effects == 1 and index == 0
        ):
            fail(f"{mode}: idempotency effect marker mismatch")

    parsed = json.loads(body)
    if list(parsed) not in (
        ["name", "storage_backings"],
        ["name", "storage_backings", "description"],
    ):
        fail(f"{mode}: top-level property order changed")
    if set(parsed) & FORBIDDEN_MODEL_FIELDS:
        fail(f"{mode}: a server-managed/unset model field was serialized")
    backing = parsed["storage_backings"][0]
    if backing["type"] == "DATASTORE":
        if list(backing) != ["type", "datastore_id"]:
            fail(f"{mode}: unset storage_uri was not omitted")
        if "description" in parsed:
            fail(f"{mode}: unset description was not omitted")
    else:
        if list(backing) != ["type", "storage_uri"]:
            fail(f"{mode}: unset datastore_id was not omitted")
        if parsed.get("description") != "":
            fail(f"{mode}: explicit empty description was not preserved")

    if len(events) == 2:
        comparable = [
            {
                key: value
                for key, value in event.items()
                if key not in {"seq", "new_effect"}
            }
            for event in events
        ]
        if comparable[0] != comparable[1]:
            fail(f"{mode}: retry request was not byte-identical")


def run_scenario(
    classes: Path,
    contract: dict[str, Any],
    scratch: Path,
    mode: str,
    behavior: str,
    nonce: str,
) -> None:
    scenario_dir = scratch / mode
    scenario_dir.mkdir()
    request_log = scenario_dir / "requests.jsonl"
    config_path = scenario_dir / "config.json"

    session_id = "session-" + secrets.token_urlsafe(18)
    client_token = str(uuid.uuid4())
    library_id = f'library/{nonce}-"\N{SNOWMAN}'
    name = f'Operations "{nonce}"\nblue \\ \N{SNOWMAN}'
    if mode == "other_empty":
        backing_value = (
            f"nfs://storage.example/library/{nonce}"
            "?label=blue%20snow"
        )
    else:
        backing_value = f"datastore/{nonce}\\primary"
    server_error = f'private "{nonce}"\nserver text'
    config_path.write_text(
        json.dumps(
            {
                "behavior": behavior,
                "session_id": session_id,
                "client_token": client_token,
                "library_id": library_id,
                "server_error": server_error,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fixture = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(MOCK),
            "--contract",
            str(CONTRACT),
            "--config",
            str(config_path),
            "--log",
            str(request_log),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        banner = read_banner(fixture)
        base_uri = f"http://{banner['host']}:{banner['port']}/"
        run = subprocess.run(
            [
                "java",
                "-cp",
                str(classes),
                "TestMain",
                mode,
                base_uri,
                session_id,
                client_token,
                library_id,
                name,
                backing_value,
                str(request_log),
                server_error,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=12,
            check=False,
        )
        if run.returncode != 0:
            fail(
                f"{mode}: TestMain failed:\n"
                + run.stdout
                + run.stderr
            )
        if run.stdout.strip() != "TEST_MAIN_OK":
            fail(f"{mode}: unexpected TestMain output")
        check_log(
            request_log,
            mode,
            session_id,
            client_token,
            expected_body(mode, name, backing_value),
        )
    finally:
        if fixture.poll() is None:
            fixture.terminate()
        try:
            fixture.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            fixture.kill()
            fixture.communicate(timeout=3)


def main() -> int:
    contract = check_contract()
    if not CLIENT.is_file():
        fail("VcenterLibraryClient.java is missing")

    with tempfile.TemporaryDirectory(prefix="vcf91-0122-") as raw_tmp:
        scratch = Path(raw_tmp)
        classes = scratch / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-encoding",
                "UTF-8",
                "-d",
                str(classes),
                str(CLIENT),
                str(TEST_MAIN),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if compile_result.returncode != 0:
            fail(
                "javac failed:\n"
                + compile_result.stdout
                + compile_result.stderr
            )

        nonce = secrets.token_hex(8)
        scenarios = [
            ("drop_after_commit", "drop_after_commit"),
            ("other_empty", "success"),
            ("http_503", "http_503"),
            ("malformed_201", "malformed_201"),
            ("drop_every", "drop_every"),
        ]
        for mode, behavior in scenarios:
            run_scenario(
                classes,
                contract,
                scratch,
                mode,
                behavior,
                nonce,
            )

    print("verification passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AssertionError,
        json.JSONDecodeError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
