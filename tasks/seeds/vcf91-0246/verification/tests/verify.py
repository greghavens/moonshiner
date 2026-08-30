#!/usr/bin/env python3
"""Protected verifier for the VCF 9.1 vSAN Data Protection inventory task.

Everything happens against an ephemeral 127.0.0.1 loopback mock that is pinned
to the protected contract. No VMware endpoint and no other network service is
contacted.
"""

from __future__ import annotations

import ast
import base64
import json
import secrets
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from mock_snapservice import MockSnapservice  # noqa: E402
from vsan_snapshot_inventory import (  # noqa: E402
    SnapserviceError,
    collect_vm_snapshot_inventory,
)


PINNED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"
SESSION_HEADER = "vmware-api-session-id"
SESSIONS_TARGET = "/api/snapservice/sessions"
SNAPSHOTS_PATH = "/api/snapservice/virtual-machines/snapshots"
SERVER_MAX_PAGE_SIZE = 20
TOTAL_SNAPSHOTS = 47

EXPECTED_OPERATIONS = [
    {
        "operationId": "Snapservice.Sessions_create",
        "method": "POST",
        "path": "/snapservice/sessions",
    },
    {
        "operationId": "Snapservice.VirtualMachines.Snapshots_list",
        "method": "GET",
        "path": "/snapservice/virtual-machines/snapshots",
    },
]

EXPECTED_QUERY_FIELDS = {
    "created_after",
    "created_before",
    "snapshots_per_vm",
    "vm_bios_uuids",
    "clusters",
    "snapshots",
    "page_size",
    "offset",
}

REPORT_ITEM_KEYS = {
    "snapshot",
    "vm",
    "name",
    "creation_time",
    "snapshot_type",
    "expiration_time",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"{path.name} is not valid UTF-8 JSON: {error}")


# -- static checks -------------------------------------------------------


def assert_contract_and_sources() -> None:
    contract = load_json(ROOT / "docs/contract.json")
    sources = load_json(ROOT / "docs/official_sources.json")

    expected_source = {
        "repository": "https://github.com/vmware/vcf-api-specs",
        "repository_commit_sha": PINNED_COMMIT,
        "spec_path": SPEC_PATH,
    }
    if contract.get("openapi") != "3.0.3":
        fail("contract must identify OpenAPI 3.0.3")
    if contract.get("spec_version") != "9.1.0.0":
        fail("contract must identify specification version 9.1.0.0")
    if contract.get("source") != expected_source:
        fail("contract source does not match the commit-pinned official source")
    if contract.get("server", {}).get("base_path") != "/api":
        fail("contract must record the /api server base path")

    if sources.get("repository") != expected_source["repository"]:
        fail("official source repository changed")
    if sources.get("license") != "Apache-2.0":
        fail("official source must record the Apache-2.0 repository license")
    if sources.get("repository_commit_sha") != PINNED_COMMIT:
        fail("official source commit changed")
    if sources.get("spec_path") != SPEC_PATH:
        fail("official specification path changed")
    source_url = sources.get("source_url", "")
    if PINNED_COMMIT not in source_url or not source_url.endswith(SPEC_PATH):
        fail("official source URL is not pinned to the recorded commit and path")

    def projection(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "operationId": item.get("operationId"),
                "method": item.get("method"),
                "path": item.get("path"),
            }
            for item in items
        ]

    if projection(contract.get("operations", [])) != EXPECTED_OPERATIONS:
        fail("contract operation set or order changed")
    if projection(sources.get("operationIds", [])) != EXPECTED_OPERATIONS:
        fail("official_sources.json does not name every scoped operationId")

    list_operation = contract["operations"][1]
    if list_operation.get("wire_path") != SNAPSHOTS_PATH:
        fail("contract wire path for the list operation changed")
    query_fields = {
        field.get("name") for field in list_operation.get("query_parameters", [])
    }
    if query_fields != EXPECTED_QUERY_FIELDS:
        fail("projected query field vocabulary differs from the pinned operation")

    schemas = contract.get("schemas", {})
    iteration = schemas.get(
        "Snapservice.VirtualMachines.Snapshots.IterationSpec", {}
    ).get("properties", {})
    if set(iteration) != {"page_size", "offset"}:
        fail("IterationSpec projection differs from the pinned specification")
    result = schemas.get("Snapservice.VirtualMachines.Snapshots.ListResult", {})
    if sorted(result.get("required", [])) != ["snapshots", "total_count"]:
        fail("ListResult required members differ from the pinned specification")
    item = schemas.get("Snapservice.VirtualMachines.Snapshots.ListItem", {})
    if sorted(item.get("required", [])) != [
        "creation_time",
        "name",
        "snapshot",
        "snapshot_type",
        "vm",
    ]:
        fail("ListItem required members differ from the pinned specification")
    if contract.get("securitySchemes", {}).get("api_key_auth", {}).get(
        "name"
    ) != SESSION_HEADER:
        fail("contract must record the vmware-api-session-id API key header")


def assert_stdlib_package() -> None:
    package = ROOT / "vsan_snapshot_inventory"
    if not package.is_dir():
        fail("vsan_snapshot_inventory package is missing")

    forbidden_transports = {"socket", "subprocess", "ssl", "ctypes"}
    for source_path in sorted(package.glob("*.py")):
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError) as error:
            fail(f"{source_path.name} cannot be parsed: {error}")

        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                if node.module:
                    names = [node.module.split(".", 1)[0]]
            for name in names:
                if name not in sys.stdlib_module_names:
                    fail(f"{source_path.name} imports non-stdlib module {name!r}")
                if name in forbidden_transports:
                    fail(
                        f"{source_path.name} uses forbidden transport module "
                        f"{name!r}"
                    )

    forbidden_suffixes = {".whl", ".zip", ".egg", ".tar", ".gz"}
    vendored = [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in forbidden_suffixes
    ]
    if vendored:
        fail(f"third-party artifacts must not be vendored: {vendored}")


# -- fixture data --------------------------------------------------------


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_dataset(cluster: str, nonce: str) -> list[dict[str, Any]]:
    """Build a snapshot inventory whose creation times repeatedly tie."""

    base = datetime(2026, 3, 2, 4, 0, tzinfo=timezone.utc)
    types = ["SCHEDULED", "ONE_TIME", "SYSTEM_CREATED", "STAGED"]
    used: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index in range(TOTAL_SNAPSHOTS):
        while True:
            snapshot_id = f"snapshot-{secrets.token_hex(5)}"
            if snapshot_id not in used:
                used.add(snapshot_id)
                break
        created = base + timedelta(minutes=17 * (index // 2))
        item: dict[str, Any] = {
            "snapshot": snapshot_id,
            "vm": f"vm-{nonce}-{index % 9:02d}",
            "name": f"daily-{nonce}-{index:03d}",
            "creation_time": stamp(created),
            "snapshot_type": types[index % 4],
        }
        if index % 3:
            item["expiration_time"] = stamp(created + timedelta(days=14))
        if index % 5 == 0:
            item["vm_bios_uuid"] = f"4210{nonce}-{index:03d}"
        if index % 7 == 0:
            item["labels"] = [{"category": "retention", "name": "gold"}]
        rows.append({"cluster": cluster, "item": item})
    return rows


def expected_report(
    rows: list[dict[str, Any]],
    cluster: str,
    *,
    created_after: str | None = None,
    created_before: str | None = None,
) -> dict[str, Any]:
    selected = [row["item"] for row in rows if row["cluster"] == cluster]
    if created_after:
        selected = [
            item for item in selected if item["creation_time"] >= created_after
        ]
    if created_before:
        selected = [
            item for item in selected if item["creation_time"] <= created_before
        ]
    selected.sort(key=lambda item: (item["creation_time"], item["snapshot"]))
    return {
        "cluster": cluster,
        "total_count": len(selected),
        "returned_count": len(selected),
        "snapshots": [
            {
                "snapshot": item["snapshot"],
                "vm": item["vm"],
                "name": item["name"],
                "creation_time": item["creation_time"],
                "snapshot_type": item["snapshot_type"],
                "expiration_time": item.get("expiration_time"),
            }
            for item in selected
        ],
    }


def expected_offsets(total: int, page_size: int) -> list[int]:
    served = min(page_size, SERVER_MAX_PAGE_SIZE)
    offsets: list[int] = []
    collected = 0
    while True:
        offsets.append(collected)
        collected += min(served, max(total - collected, 0))
        if collected >= total:
            return offsets


# -- wire assertions -----------------------------------------------------


def read_requests(log_path: Path) -> list[dict[str, Any]]:
    try:
        return [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as error:
        fail(f"mock request log is unreadable: {error}")


def assert_session_request(
    request: dict[str, Any], *, username: str, password: str
) -> None:
    if request["method"] != "POST" or request["target"] != SESSIONS_TARGET:
        fail(
            "the first request must be Snapservice.Sessions_create as "
            f"POST {SESSIONS_TARGET}, observed "
            f"{request['method']} {request['target']}"
        )
    raw = f"{username}:{password}".encode("utf-8")
    expected = "Basic " + base64.b64encode(raw).decode("ascii")
    if request["headers"].get("authorization") != expected:
        fail("Snapservice.Sessions_create must send HTTP Basic credentials")
    if SESSION_HEADER in request["headers"]:
        fail(f"Snapservice.Sessions_create must not send {SESSION_HEADER}")
    if request["body"] != "":
        fail("Snapservice.Sessions_create declares no request body")
    if request["headers"].get("content-length", "0") != "0":
        fail("Snapservice.Sessions_create must not send a request body")


def assert_list_request(
    request: dict[str, Any],
    *,
    cluster: str,
    page_size: int,
    offset: int,
    token: str,
    extra: dict[str, str],
) -> None:
    if request["method"] != "GET":
        fail(
            "Snapservice.VirtualMachines.Snapshots_list is a GET operation, "
            f"observed {request['method']}"
        )
    split = urlsplit(request["target"])
    if split.path != SNAPSHOTS_PATH:
        fail(f"list request path must be {SNAPSHOTS_PATH}, observed {split.path}")
    if split.fragment:
        fail("list request must not carry a fragment")

    pairs = parse_qsl(split.query, keep_blank_values=True)
    for name, value in pairs:
        if value == "":
            fail(
                f"query field {name!r} was sent with an empty value; unset "
                "optional fields must be omitted entirely"
            )
        if name not in EXPECTED_QUERY_FIELDS:
            fail(f"query field {name!r} is not part of the pinned operation")
    names = [name for name, _ in pairs]
    if len(names) != len(set(names)):
        fail(f"list request repeated a single-valued query field: {names}")

    expected = {
        "clusters": cluster,
        "page_size": str(page_size),
        "offset": str(offset),
    }
    expected.update(extra)
    if dict(pairs) != expected:
        fail(
            "list request query fields differ from the pinned wire shape: "
            f"expected {sorted(expected.items())}, observed {sorted(pairs)}"
        )

    if request["headers"].get(SESSION_HEADER) != token:
        fail(f"list request must authenticate with the {SESSION_HEADER} header")
    if "authorization" in request["headers"]:
        fail("list request must not resend HTTP Basic credentials")
    if request["headers"].get("accept") != "application/json":
        fail("list request must send Accept: application/json")
    if request["body"] != "":
        fail("list request must be bodyless")


def assert_report_file(
    path: Path, expected: dict[str, Any], *, secrets_to_reject: tuple[str, ...]
) -> None:
    try:
        raw = path.read_bytes()
    except OSError as error:
        fail(f"report was not written: {error}")
    if raw.startswith(b"\xef\xbb\xbf"):
        fail("report must be UTF-8 without a BOM")
    if b"\r" in raw:
        fail("report must use LF line endings")
    if not raw.endswith(b"\n"):
        fail("report must end with LF")
    try:
        text = raw.decode("utf-8")
        stored = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"report is not valid UTF-8 JSON: {error}")
    if stored != expected:
        fail("stored report differs from the expected stable inventory")
    for secret in secrets_to_reject:
        if secret and secret in text:
            fail("report leaked a credential or session token")


def assert_report_value(returned: Any, expected: dict[str, Any], label: str) -> None:
    if not isinstance(returned, dict):
        fail(f"{label}: the collector must return the report dictionary")
    if set(returned) != set(expected):
        fail(
            f"{label}: report members differ, expected {sorted(expected)}, "
            f"observed {sorted(returned)}"
        )
    for entry in returned.get("snapshots", []):
        if not isinstance(entry, dict) or set(entry) != REPORT_ITEM_KEYS:
            fail(
                f"{label}: each snapshot entry must carry exactly "
                f"{sorted(REPORT_ITEM_KEYS)}"
            )
    if returned != expected:
        if returned.get("returned_count") != expected["returned_count"]:
            fail(
                f"{label}: collected {returned.get('returned_count')!r} of "
                f"{expected['returned_count']} snapshots; every page must be "
                "retrieved"
            )
        fail(f"{label}: report contents or snapshot order differ from expected")


def assert_snapservice_failure(
    action: Any,
    *,
    label: str,
    report_path: Path,
    original_report: bytes,
    secrets_to_reject: tuple[str, ...],
) -> None:
    """Require a sanitized service failure that leaves the report untouched."""

    try:
        action()
    except SnapserviceError as error:
        message = str(error)
        for secret in secrets_to_reject:
            if secret and secret in message:
                fail(f"{label}: SnapserviceError leaked protected response data")
    except Exception as error:  # noqa: BLE001
        fail(
            f"{label}: expected SnapserviceError, observed "
            f"{type(error).__name__}"
        )
    else:
        fail(f"{label}: expected SnapserviceError")

    try:
        observed = report_path.read_bytes()
    except OSError as error:
        fail(f"{label}: the pre-existing report disappeared: {error}")
    if observed != original_report:
        fail(f"{label}: a failed collection modified the pre-existing report")


# -- scenarios -----------------------------------------------------------


class Harness:
    """One mock lifetime with freshly generated credentials and data."""

    def __init__(self, temp: Path, name: str) -> None:
        nonce = secrets.token_hex(4)
        self.temp = temp / name
        self.temp.mkdir(parents=True)
        self.log_path = self.temp / "requests.jsonl"
        self.log_path.touch()
        self.cluster = f"domain-c{secrets.randbelow(900) + 100}"
        self.empty_cluster = f"domain-c{secrets.randbelow(900) + 1000}"
        self.username = f"svc-{nonce}@vsphere.local"
        self.password = f"pw-{secrets.token_urlsafe(12)}"
        self.token = secrets.token_urlsafe(20)
        self.dataset = build_dataset(self.cluster, nonce)

    def mock(self) -> MockSnapservice:
        return MockSnapservice(
            ROOT / "docs/contract.json",
            self.log_path,
            dataset=self.dataset,
            username=self.username,
            password=self.password,
            session_token=self.token,
            max_page_size=SERVER_MAX_PAGE_SIZE,
        )

    def requests(self) -> list[dict[str, Any]]:
        return read_requests(self.log_path)


def scenario_full_inventory(temp: Path) -> None:
    harness = Harness(temp, "full")
    report_path = harness.temp / "nested" / "inventory.json"
    page_size = 25
    expected = expected_report(harness.dataset, harness.cluster)

    with harness.mock() as mock:
        try:
            returned = collect_vm_snapshot_inventory(
                mock.service_root,
                harness.username,
                harness.password,
                harness.cluster,
                report_path,
                page_size=page_size,
                created_after=None,
                created_before="",
                timeout=2.0,
            )
        except Exception as error:  # noqa: BLE001
            fail(
                "full inventory scenario raised "
                f"{type(error).__name__}: {error}"
            )

    requests = harness.requests()
    offsets = expected_offsets(TOTAL_SNAPSHOTS, page_size)
    if len(requests) != 1 + len(offsets):
        fail(
            f"expected 1 session request and {len(offsets)} page requests, "
            f"observed {len(requests)} requests in total"
        )
    assert_session_request(
        requests[0], username=harness.username, password=harness.password
    )
    for request, offset in zip(requests[1:], offsets):
        assert_list_request(
            request,
            cluster=harness.cluster,
            page_size=page_size,
            offset=offset,
            token=harness.token,
            extra={},
        )

    assert_report_value(returned, expected, "full inventory")
    assert_report_file(
        report_path,
        expected,
        secrets_to_reject=(harness.password, harness.token),
    )


def scenario_filtered(temp: Path) -> None:
    harness = Harness(temp, "filtered")
    report_path = harness.temp / "filtered.json"
    replacement_witness = harness.temp / "previous-report.json"
    previous_report = b'{"status":"previous-success"}\n'
    report_path.write_bytes(previous_report)
    replacement_witness.hardlink_to(report_path)
    page_size = 5
    cutoff = harness.dataset[34]["item"]["creation_time"]
    ceiling = harness.dataset[44]["item"]["creation_time"]
    expected = expected_report(
        harness.dataset,
        harness.cluster,
        created_after=cutoff,
        created_before=ceiling,
    )
    if expected["total_count"] != 12:
        fail("verifier fixture drifted: the filtered inventory must hold 12 items")

    with harness.mock() as mock:
        try:
            returned = collect_vm_snapshot_inventory(
                mock.service_root,
                harness.username,
                harness.password,
                harness.cluster,
                report_path,
                page_size=page_size,
                created_after=cutoff,
                created_before=ceiling,
                timeout=2.0,
            )
        except Exception as error:  # noqa: BLE001
            fail(f"filtered scenario raised {type(error).__name__}: {error}")

    requests = harness.requests()
    offsets = expected_offsets(expected["total_count"], page_size)
    if len(requests) != 1 + len(offsets):
        fail(
            f"expected 1 session request and {len(offsets)} page requests, "
            f"observed {len(requests)} requests in total"
        )
    assert_session_request(
        requests[0], username=harness.username, password=harness.password
    )
    for request, offset in zip(requests[1:], offsets):
        assert_list_request(
            request,
            cluster=harness.cluster,
            page_size=page_size,
            offset=offset,
            token=harness.token,
            extra={"created_after": cutoff, "created_before": ceiling},
        )

    assert_report_value(returned, expected, "filtered inventory")
    assert_report_file(
        report_path,
        expected,
        secrets_to_reject=(harness.password, harness.token),
    )
    if replacement_witness.read_bytes() != previous_report:
        fail("the report was rewritten in place instead of atomically replaced")
    if report_path.stat().st_ino == replacement_witness.stat().st_ino:
        fail("the completed report still aliases the pre-replacement file")


def scenario_empty(temp: Path) -> None:
    harness = Harness(temp, "empty")
    report_path = harness.temp / "empty.json"
    expected = expected_report(harness.dataset, harness.empty_cluster)
    if expected["total_count"] != 0:
        fail("verifier fixture drifted: the empty cluster must hold no snapshots")

    with harness.mock() as mock:
        try:
            returned = collect_vm_snapshot_inventory(
                mock.service_root,
                harness.username,
                harness.password,
                harness.empty_cluster,
                report_path,
                page_size=12,
                timeout=2.0,
            )
        except Exception as error:  # noqa: BLE001
            fail(f"empty cluster scenario raised {type(error).__name__}: {error}")

    requests = harness.requests()
    if len(requests) != 2:
        fail(
            "an empty collection still needs one session request and exactly "
            f"one page request, observed {len(requests)} requests"
        )
    assert_session_request(
        requests[0], username=harness.username, password=harness.password
    )
    assert_list_request(
        requests[1],
        cluster=harness.empty_cluster,
        page_size=12,
        offset=0,
        token=harness.token,
        extra={},
    )
    assert_report_value(returned, expected, "empty cluster")
    assert_report_file(
        report_path,
        expected,
        secrets_to_reject=(harness.password, harness.token),
    )


def scenario_rejected_credentials(temp: Path) -> None:
    harness = Harness(temp, "unauthenticated")
    report_path = harness.temp / "should-not-exist.json"

    with harness.mock() as mock:
        try:
            collect_vm_snapshot_inventory(
                mock.service_root,
                harness.username,
                harness.password + "-wrong",
                harness.cluster,
                report_path,
                timeout=2.0,
            )
        except SnapserviceError as error:
            message = str(error)
            if harness.password in message or harness.token in message:
                fail("SnapserviceError leaked a credential or session token")
        except Exception as error:  # noqa: BLE001
            fail(
                "a rejected session must raise SnapserviceError, observed "
                f"{type(error).__name__}"
            )
        else:
            fail("a rejected session must raise SnapserviceError")

    requests = harness.requests()
    if len(requests) != 1:
        fail(
            "a rejected session must stop before listing snapshots, observed "
            f"{len(requests)} requests"
        )
    if report_path.exists():
        fail("no report may be written when the session is rejected")


def scenario_early_empty_page(temp: Path) -> None:
    harness = Harness(temp, "early-empty")
    report_path = harness.temp / "existing.json"
    original_report = b'{"status":"previous"}\n'
    report_path.write_bytes(original_report)

    mock = harness.mock()
    normal_list = mock._list_snapshots

    def list_with_early_empty(query: str) -> tuple[int, dict[str, Any]]:
        fields = dict(parse_qsl(query, keep_blank_values=True))
        if fields.get("offset") == str(SERVER_MAX_PAGE_SIZE):
            return 200, {"snapshots": [], "total_count": TOTAL_SNAPSHOTS}
        return normal_list(query)

    mock._list_snapshots = list_with_early_empty  # type: ignore[method-assign]
    with mock:
        assert_snapservice_failure(
            lambda: collect_vm_snapshot_inventory(
                mock.service_root,
                harness.username,
                harness.password,
                harness.cluster,
                report_path,
                page_size=25,
                timeout=2.0,
            ),
            label="early empty page",
            report_path=report_path,
            original_report=original_report,
            secrets_to_reject=(harness.password, harness.token),
        )

    requests = harness.requests()
    if len(requests) != 3:
        fail(
            "an early empty page must stop collection after offsets 0 and 20, "
            f"observed {len(requests)} total requests"
        )
    assert_session_request(
        requests[0], username=harness.username, password=harness.password
    )
    for request, offset in zip(requests[1:], (0, SERVER_MAX_PAGE_SIZE)):
        assert_list_request(
            request,
            cluster=harness.cluster,
            page_size=25,
            offset=offset,
            token=harness.token,
            extra={},
        )


def scenario_malformed_responses(temp: Path) -> None:
    # A schema-invalid session body must not be accepted as a token.
    session_harness = Harness(temp, "malformed-session")
    session_report = session_harness.temp / "existing.json"
    original_session_report = b'{"status":"previous-session"}\n'
    session_report.write_bytes(original_session_report)
    body_secret = f"body-secret-{session_harness.token}"
    session_mock = session_harness.mock()
    session_mock.session_token = {
        "unexpected_token": body_secret,
        "echoed_password": session_harness.password,
    }
    with session_mock:
        assert_snapservice_failure(
            lambda: collect_vm_snapshot_inventory(
                session_mock.service_root,
                session_harness.username,
                session_harness.password,
                session_harness.cluster,
                session_report,
                timeout=2.0,
            ),
            label="malformed session body",
            report_path=session_report,
            original_report=original_session_report,
            secrets_to_reject=(
                session_harness.password,
                session_harness.token,
                body_secret,
            ),
        )
    session_requests = session_harness.requests()
    if len(session_requests) != 1:
        fail("a malformed session body must stop before the list operation")
    assert_session_request(
        session_requests[0],
        username=session_harness.username,
        password=session_harness.password,
    )

    # A list item whose optional expiration_time has the wrong JSON type is
    # malformed under the pinned response schema.
    list_harness = Harness(temp, "malformed-list")
    list_report = list_harness.temp / "existing.json"
    original_list_report = b'{"status":"previous-list"}\n'
    list_report.write_bytes(original_list_report)
    list_mock = list_harness.mock()
    malformed_item = dict(list_harness.dataset[0]["item"])
    malformed_item["expiration_time"] = {"secret": list_harness.token}

    def malformed_list(_query: str) -> tuple[int, dict[str, Any]]:
        return 200, {"snapshots": [malformed_item], "total_count": 1}

    list_mock._list_snapshots = malformed_list  # type: ignore[method-assign]
    with list_mock:
        assert_snapservice_failure(
            lambda: collect_vm_snapshot_inventory(
                list_mock.service_root,
                list_harness.username,
                list_harness.password,
                list_harness.cluster,
                list_report,
                timeout=2.0,
            ),
            label="malformed list body",
            report_path=list_report,
            original_report=original_list_report,
            secrets_to_reject=(list_harness.password, list_harness.token),
        )
    list_requests = list_harness.requests()
    if len(list_requests) != 2:
        fail("a malformed first list page must stop after one list request")
    assert_session_request(
        list_requests[0],
        username=list_harness.username,
        password=list_harness.password,
    )
    assert_list_request(
        list_requests[1],
        cluster=list_harness.cluster,
        page_size=25,
        offset=0,
        token=list_harness.token,
        extra={},
    )


def scenario_non_success_redirect(temp: Path) -> None:
    harness = Harness(temp, "redirect")
    report_path = harness.temp / "existing.json"
    original_report = b'{"status":"previous-redirect"}\n'
    report_path.write_bytes(original_report)
    response_secret = f"redirect-body-{harness.token}"
    mock = harness.mock()

    def redirect_list(
        _query: str,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        return (
            302,
            {"message": response_secret, "password": harness.password},
            {"Location": "/redirect-target"},
        )

    mock._list_snapshots = redirect_list  # type: ignore[method-assign]
    with mock:
        assert_snapservice_failure(
            lambda: collect_vm_snapshot_inventory(
                mock.service_root,
                harness.username,
                harness.password,
                harness.cluster,
                report_path,
                timeout=2.0,
            ),
            label="non-success redirect",
            report_path=report_path,
            original_report=original_report,
            secrets_to_reject=(harness.password, harness.token, response_secret),
        )

    requests = harness.requests()
    if len(requests) != 2:
        fail(
            "a non-2xx list response must not be followed, observed "
            f"{len(requests) - 2} extra request(s)"
        )
    assert_session_request(
        requests[0], username=harness.username, password=harness.password
    )
    assert_list_request(
        requests[1],
        cluster=harness.cluster,
        page_size=25,
        offset=0,
        token=harness.token,
        extra={},
    )


def scenario_local_validation(temp: Path) -> None:
    harness = Harness(temp, "validation")
    report_path = harness.temp / "unwritten.json"

    with harness.mock() as mock:
        root = mock.service_root
        host = root.split("//", 1)[1]
        invalid = [
            ("a service root without a host", "http:///", {}),
            ("a malformed port", "http://127.0.0.1:not-a-port", {}),
            ("whitespace in the origin", f"http://127.0.0.1 :{host.rsplit(':', 1)[1]}", {}),
            ("credentials in the service root", f"http://user:pw@{host}", {}),
            ("a query in the service root", f"{root}/?limit=1", {}),
            ("a path in the service root", f"{root}/api", {}),
            ("a fragment in the service root", f"{root}/#frag", {}),
            ("an unsupported scheme", f"ftp://{host}", {}),
            ("an empty cluster identifier", root, {"cluster": ""}),
            ("a zero page size", root, {"page_size": 0}),
            ("a negative page size", root, {"page_size": -5}),
            ("a boolean page size", root, {"page_size": True}),
            ("a string page size", root, {"page_size": "25"}),
        ]
        for label, service_root, overrides in invalid:
            cluster = overrides.get("cluster", harness.cluster)
            page_size = overrides.get("page_size", 25)
            try:
                collect_vm_snapshot_inventory(
                    service_root,
                    harness.username,
                    harness.password,
                    cluster,
                    report_path,
                    page_size=page_size,
                    timeout=2.0,
                )
            except ValueError:
                continue
            except Exception as error:  # noqa: BLE001
                fail(
                    f"{label} must raise ValueError, observed "
                    f"{type(error).__name__}"
                )
            fail(f"{label} was accepted instead of raising ValueError")

    if harness.requests():
        fail("local validation must reject input before contacting the appliance")
    if report_path.exists():
        fail("local validation failures must not write a report")


def main() -> int:
    try:
        assert_contract_and_sources()
        assert_stdlib_package()
        with tempfile.TemporaryDirectory(prefix="vcf91-0246-") as temp_dir:
            temp = Path(temp_dir)
            scenario_full_inventory(temp)
            scenario_filtered(temp)
            scenario_empty(temp)
            scenario_rejected_credentials(temp)
            scenario_early_empty_page(temp)
            scenario_malformed_responses(temp)
            scenario_non_success_redirect(temp)
            scenario_local_validation(temp)
    except (AssertionError, OSError) as error:
        print(f"VERIFICATION FAILED: {error}", file=sys.stderr)
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
