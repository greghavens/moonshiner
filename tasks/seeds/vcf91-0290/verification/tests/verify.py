#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0290.

Starts a contract-pinned loopback appliance on an ephemeral 127.0.0.1 port,
runs the rollout three times with runtime-generated identifiers, and asserts
the exact request wire shape from the mock's flushed JSONL request log.
No live VMware endpoint is contacted.
"""

from __future__ import annotations

import ast
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
PACKAGE_INIT = ROOT / "vcfon_tiers" / "__init__.py"
SOLUTION_PATH = ROOT / "vcfon_tiers" / "rollout.py"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
RUNNER_PATH = ROOT / ".protected" / "run_case.py"

COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
OPERATION_IDS = ["create", "listApplications", "getApplicationById", "addTier", "delete"]
ROUTES = [
    ("POST", "/auth/token"),
    ("GET", "/groups/applications"),
    ("GET", "/groups/applications/{id}"),
    ("POST", "/groups/applications/{id}/tiers"),
    ("DELETE", "/auth/token"),
]
BASE_PATH = "/api/ni"
AUTH_PREFIX = "NetworkInsight "

PAGE_SIZE = 3
APPLICATION_COUNT = 7
PLANNED_INDEXES = [0, 1, 3, 4, 6]
EXPIRE_AFTER_TIER_COUNT = 3

REPORT_KEYS = [
    "base_url",
    "pages_fetched",
    "applications_listed",
    "tiers_created",
    "applications_without_plan",
    "plan_entries_without_application",
    "token_refreshes",
]
TIER_RECORD_KEYS = [
    "application_entity_id",
    "application_name",
    "tier_entity_id",
    "tier_name",
]

# Independent oracle for the specification's request-side property order.
USER_CREDENTIAL_ORDER = ("username", "password", "domain")
DOMAIN_ORDER = ("domain_type", "value")
TIER_REQUEST_ORDER = (
    "name",
    "entity_id",
    "group_membership_criteria",
    "member_list",
    "source_group_entity_id",
)
CRITERIA_ORDER = (
    "membership_type",
    "ip_address_membership_criteria",
    "search_membership_criteria",
)
IP_CRITERIA_ORDER = ("ip_addresses",)
SEARCH_CRITERIA_ORDER = ("entity_type", "filter")
MEMBER_LIST_ORDER = ("vms", "physical_ips", "kubernetes_services")
MEMBER_ORDER = ("key", "name")
REFERENCE_ORDER = ("entity_id", "entity_type", "entity_name")

BANNED_IMPORT_ROOTS = {
    "requests",
    "httpx",
    "urllib3",
    "aiohttp",
    "httplib2",
    "yaml",
    "pytest",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def encode(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------------
# Oracle projection
# --------------------------------------------------------------------------


def project(value: Any, order: tuple[str, ...], nested: dict[str, Any] | None = None) -> dict:
    out: dict[str, Any] = {}
    for key in order:
        if key not in value or value[key] is None:
            continue
        item = value[key]
        projector = (nested or {}).get(key)
        out[key] = projector(item) if projector else item
    return out


def project_member(value: Any) -> dict:
    return project(value, MEMBER_ORDER, {"key": lambda v: project(v, REFERENCE_ORDER)})


def project_member_list(value: Any) -> dict:
    return project(
        value,
        MEMBER_LIST_ORDER,
        {name: lambda v: [project_member(i) for i in v] for name in MEMBER_LIST_ORDER},
    )


def project_criterion(value: Any) -> dict:
    return project(
        value,
        CRITERIA_ORDER,
        {
            "ip_address_membership_criteria": lambda v: project(v, IP_CRITERIA_ORDER),
            "search_membership_criteria": lambda v: project(v, SEARCH_CRITERIA_ORDER),
        },
    )


def project_tier_request(value: Any) -> dict:
    return project(
        value,
        TIER_REQUEST_ORDER,
        {
            "group_membership_criteria": lambda v: [project_criterion(i) for i in v],
            "member_list": project_member_list,
        },
    )


def project_credential(username: str, password: str, domain: Any) -> dict:
    raw: dict[str, Any] = {"username": username, "password": password}
    if domain is not None:
        raw["domain"] = domain
    return project(
        raw, USER_CREDENTIAL_ORDER, {"domain": lambda v: project(v, DOMAIN_ORDER)}
    )


# --------------------------------------------------------------------------
# Static checks
# --------------------------------------------------------------------------


def verify_contract() -> None:
    require(CONTRACT_PATH.is_file(), "docs/contract.json is missing")
    require(SOURCES_PATH.is_file(), "docs/official_sources.json is missing")
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)

    source = contract.get("source", {})
    require(source.get("repositoryCommitSha") == COMMIT, "contract commit is not pinned")
    require(source.get("specPath") == SPEC_PATH, "contract spec path is not pinned")
    require(source.get("serverBasePath") == BASE_PATH, "contract server base path changed")
    operations = contract.get("operations", [])
    require(
        [item.get("operationId") for item in operations] == OPERATION_IDS,
        "contract operationIds changed",
    )
    require(
        [(item.get("method"), item.get("path")) for item in operations] == ROUTES,
        "contract routes changed",
    )
    security = contract.get("security", {})
    require(
        security.get("headerName") == "Authorization"
        and security.get("valueFormat") == "NetworkInsight {token}"
        and security.get("unauthenticatedOperationIds") == ["create"],
        "contract security projection changed",
    )
    schemas = contract.get("schemas", {})
    for name, expected in (
        ("UserCredential", list(USER_CREDENTIAL_ORDER)),
        ("Domain", list(DOMAIN_ORDER)),
        ("TierRequest", list(TIER_REQUEST_ORDER)),
        ("GroupMembershipCriteria", list(CRITERIA_ORDER)),
        ("SearchMembershipCriteria", list(SEARCH_CRITERIA_ORDER)),
        ("IpAddressMembershipCriteria", list(IP_CRITERIA_ORDER)),
        ("MemberList", list(MEMBER_LIST_ORDER)),
        ("Member", list(MEMBER_ORDER)),
        ("Reference", list(REFERENCE_ORDER)),
    ):
        require(
            schemas.get(name, {}).get("propertyOrder") == expected,
            f"contract propertyOrder for {name} changed",
        )

    require(sources.get("repositoryCommitSha") == COMMIT, "sources commit is not pinned")
    require(sources.get("specPath") == SPEC_PATH, "sources spec path is not pinned")
    require(sources.get("operationIds") == OPERATION_IDS, "sources operationIds changed")
    require(
        sources.get("derivation", {}).get("documentationPageUsedAsContractSource")
        is False,
        "the contract must be projected from the specification",
    )
    recorded = sources.get("operations", [])
    require(len(recorded) == len(OPERATION_IDS), "sources operation records are incomplete")
    for item in recorded:
        require(
            item.get("repositoryCommitSha") == COMMIT
            and item.get("specPath") == SPEC_PATH
            and isinstance(item.get("jsonPointer"), str)
            and item["jsonPointer"].endswith("/operationId"),
            f"source record for {item.get('operationId')} is incomplete",
        )


def verify_solution_shape() -> None:
    require(SOLUTION_PATH.is_file(), "vcfon_tiers/rollout.py is missing")
    require(PACKAGE_INIT.is_file(), "vcfon_tiers/__init__.py is missing")
    source = SOLUTION_PATH.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise VerificationError(f"vcfon_tiers/rollout.py does not parse: {error}") from error

    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    banned = sorted(roots & BANNED_IMPORT_ROOTS)
    require(not banned, f"rollout.py imports non-stdlib modules: {banned}")
    outside = sorted(
        name
        for name in roots
        if name not in sys.stdlib_module_names and name != "vcfon_tiers"
    )
    require(not outside, f"rollout.py imports modules outside the standard library: {outside}")

    for needle in (".protected", "mock_server", "request_log", "requests.jsonl"):
        require(
            needle not in source,
            f"rollout.py must not reference protected verification material ({needle})",
        )

    names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for required_name in ("run_tier_rollout", "VcfOnError", "ApiError", "TokenRefreshError"):
        require(required_name in names, f"rollout.py does not define {required_name}")


# --------------------------------------------------------------------------
# Scenario construction
# --------------------------------------------------------------------------


def build_scenario(mode: str, nonce: str) -> dict[str, Any]:
    reserved = ":/?#[]@!$&'()*+,;=%"
    applications = []
    for index in range(APPLICATION_COUNT):
        applications.append(
            {
                # Deliberately exercise every RFC 3986 reserved character in a
                # single path segment, plus a non-ASCII character.
                "entity_id": (
                    f"18230{reserved}561{reserved}{100000 + index}-{nonce}-京"
                ),
                "name": f"app-{nonce}-{index}",
                "created_by": f"admin-{nonce}@local",
                "create_time": 1509410056733 + index,
            }
        )
    return {
        "mode": mode,
        "expireAfterTierCount": EXPIRE_AFTER_TIER_COUNT,
        "credentials": {
            "username": f"admin-{nonce}@vrni.local",
            "password": f"pw-{nonce}",
        },
        "mintableTokens": [f"tok{index}-{nonce}" for index in range(4)],
        "tokenExpiries": [1605201960327 + index for index in range(4)],
        "applications": applications,
        "tierIdPrefix": f"18230:562:{nonce}-",
    }


def build_plan(scenario: dict[str, Any], nonce: str) -> dict[str, Any]:
    """Plan entries deliberately carry members out of specification order."""
    names = [item["name"] for item in scenario["applications"]]
    plan: dict[str, Any] = {}
    plan[names[0]] = {
        "entity_id": None,
        "group_membership_criteria": [
            {
                "ip_address_membership_criteria": None,
                "search_membership_criteria": {
                    "filter": f"security_groups.entity_id = '{nonce}'",
                    "entity_type": "VirtualMachine",
                },
                "membership_type": "SearchMembershipCriteria",
            }
        ],
        "name": f"tier-web-{nonce}",
    }
    plan[names[1]] = {
        "member_list": {
            "physical_ips": None,
            "vms": [
                {
                    "name": f"vm-café-{nonce}",
                    "key": {
                        "entity_type": "VIRTUALMACHINE",
                        "entity_id": f"18230:1:{nonce}a",
                        "entity_name": None,
                    },
                }
            ],
        },
        "name": f"tier-app-é-{nonce}",
        "source_group_entity_id": None,
        "group_membership_criteria": [
            {
                "ip_address_membership_criteria": {
                    "ip_addresses": ["10.0.0.1", "10.0.0.1/24", "10.0.0.1-10.0.0.200"]
                },
                "membership_type": "IPAddressMembershipCriteria",
            }
        ],
    }
    plan[names[3]] = {
        "source_group_entity_id": [f"18230:566:{nonce}b"],
        "member_list": None,
        "name": f"tier-db-{nonce}",
        "group_membership_criteria": [
            {
                "search_membership_criteria": {
                    "filter": f"name like '{nonce}'",
                    "entity_type": "VirtualMachine",
                },
                "membership_type": "SearchMembershipCriteria",
            }
        ],
    }
    plan[names[4]] = {
        "entity_id": None,
        "group_membership_criteria": None,
        "member_list": {
            "vms": None,
            "physical_ips": [
                {
                    "key": {
                        "entity_id": f"18230:541:{nonce}c",
                        "entity_type": "IPENDPOINT",
                        "entity_name": None,
                    },
                    "name": "52.35.41.245",
                }
            ],
            "kubernetes_services": None,
        },
        "name": f"tier-edge-京-{nonce}",
    }
    plan[names[6]] = {
        "source_group_entity_id": [f"18230:566:{nonce}d", f"18230:566:{nonce}e"],
        "member_list": {
            "physical_ips": None,
            "kubernetes_services": [
                {
                    "name": f"ks-{nonce}",
                    "key": {
                        "entity_id": f"18230:1504:{nonce}f",
                        "entity_type": "KUBERNETESSERVICE",
                        "entity_name": None,
                    },
                }
            ],
            "vms": [
                {
                    "key": {
                        "entity_id": f"18230:1702:{nonce}g",
                        "entity_type": "AZUREVM",
                        "entity_name": None,
                    },
                    "name": f"azure-{nonce}",
                }
            ],
        },
        "name": f"tier-full-{nonce}",
        "group_membership_criteria": [
            {
                "membership_type": "SearchMembershipCriteria",
                "ip_address_membership_criteria": None,
                "search_membership_criteria": {
                    "entity_type": "VirtualMachine",
                    "filter": f"tag = '{nonce}'",
                },
            },
            {
                "ip_address_membership_criteria": {"ip_addresses": ["192.0.2.0/24"]},
                "membership_type": "IPAddressMembershipCriteria",
                "search_membership_criteria": None,
            },
        ],
    }
    # Two applications the appliance does not have, deliberately inserted out
    # of sorted order.
    for phantom in (f"zz-absent-{nonce}", f"aa-absent-{nonce}"):
        plan[phantom] = {
            "name": f"tier-phantom-{phantom}",
            "group_membership_criteria": [
                {
                    "membership_type": "SearchMembershipCriteria",
                    "search_membership_criteria": {
                        "entity_type": "VirtualMachine",
                        "filter": "name like 'nothing'",
                    },
                }
            ],
        }
    return plan


# --------------------------------------------------------------------------
# Runtime harness
# --------------------------------------------------------------------------


class Appliance:
    def __init__(self, workdir: Path, scenario: dict[str, Any], label: str) -> None:
        self.scenario_path = workdir / f"scenario-{label}.json"
        self.log_path = workdir / f"requests-{label}.jsonl"
        self.scenario_path.write_text(
            json.dumps(scenario, ensure_ascii=False), encoding="utf-8"
        )
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                str(CONTRACT_PATH),
                str(self.scenario_path),
                str(self.log_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        line = self.process.stdout.readline() if self.process.stdout else ""
        if not line.strip():
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise VerificationError(f"the loopback appliance did not start: {stderr.strip()}")
        self.port = json.loads(line)["port"]
        self.base_url = f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)

    def entries(self) -> list[dict[str, Any]]:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            text = self.log_path.read_text(encoding="utf-8")
            if text.endswith("\n") or not text:
                break
            time.sleep(0.05)
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def run_case(workdir: Path, label: str, case: dict[str, Any]) -> dict[str, Any]:
    case_path = workdir / f"case-{label}.json"
    result_path = workdir / f"result-{label}.json"
    case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-B", str(RUNNER_PATH), str(case_path), str(result_path)],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
    )
    if not result_path.is_file():
        raise VerificationError(
            f"case {label} produced no result: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return load_json(result_path)


def one_header(entry: dict[str, Any], name: str) -> str:
    values = entry.get("headerValues", {}).get(name, [])
    require(len(values) == 1, f"{entry.get('operationId')} must send exactly one {name} header")
    return values[0]


def no_header(entry: dict[str, Any], name: str) -> None:
    values = entry.get("headerValues", {}).get(name, [])
    require(
        not values,
        f"{entry.get('operationId')} must not send a {name} header",
    )


def check_common(entry: dict[str, Any]) -> None:
    accept = one_header(entry, "accept")
    require(
        accept == "application/json",
        f"{entry.get('operationId')} must send Accept: application/json exactly",
    )


def check_body(entry: dict[str, Any], expected: str, label: str) -> None:
    require(entry.get("body") == expected, f"{label} body bytes changed:\n  sent {entry.get('body')!r}\n  want {expected!r}")
    require(
        entry.get("bodyLength") == len(expected.encode("utf-8")),
        f"{label} Content-Length must count UTF-8 bytes",
    )
    content_type = one_header(entry, "content-type")
    require(
        content_type == "application/json",
        f"{label} must send Content-Type: application/json exactly",
    )


def check_no_body(entry: dict[str, Any], label: str) -> None:
    require(entry.get("bodyLength") == 0 and entry.get("body") == "", f"{label} must send no body")
    no_header(entry, "content-type")


def check_token_create(entry: dict[str, Any], expected_body: str, label: str) -> None:
    require(entry.get("operationId") == "create", f"{label} must be the create operation")
    require(entry.get("method") == "POST", f"{label} must be a POST")
    require(entry.get("rawTarget") == f"{BASE_PATH}/auth/token", f"{label} target changed")
    require(entry.get("rawQuery") == "", f"{label} must send no query string")
    check_common(entry)
    no_header(entry, "authorization")
    check_body(entry, expected_body, label)
    require(entry.get("responseStatus") == 200, f"{label} did not succeed")


def check_authorized(entry: dict[str, Any], token_index: int, label: str) -> None:
    value = one_header(entry, "authorization")
    require(
        value.startswith(AUTH_PREFIX),
        f"{label} Authorization must use the NetworkInsight scheme, got {value!r}",
    )
    require(
        entry.get("presentedTokenIndex") == token_index,
        f"{label} presented token #{entry.get('presentedTokenIndex')}, expected #{token_index}",
    )


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------


def verify_rollout_case(
    workdir: Path, label: str, domain: Any, *, trailing_slash: bool = False
) -> None:
    nonce = secrets.token_hex(4)
    scenario = build_scenario("expire_after_tiers", nonce)
    plan = build_plan(scenario, nonce)
    applications = scenario["applications"]
    credentials = scenario["credentials"]

    appliance = Appliance(workdir, scenario, label)
    try:
        result = run_case(
            workdir,
            label,
            {
                "base_url": appliance.base_url + ("/" if trailing_slash else ""),
                "username": credentials["username"],
                "password": credentials["password"],
                "plan": plan,
                "domain": domain,
                "page_size": PAGE_SIZE,
            },
        )
        entries = appliance.entries()
    finally:
        appliance.stop()

    require(
        result.get("status") == "ok",
        f"case {label} raised {result.get('errorType')}: {result.get('errorMessage')}\n"
        f"{result.get('traceback', '')}",
    )

    expected_credential = encode(
        project_credential(credentials["username"], credentials["password"], domain)
    )
    if domain is None:
        require(
            '"domain"' not in expected_credential,
            "the oracle credential must omit an unset domain",
        )

    # -- request sequence ------------------------------------------------
    expected_sequence = (
        ["create"]
        + ["listApplications"] * 3
        + ["getApplicationById"] * APPLICATION_COUNT
        + ["addTier"] * EXPIRE_AFTER_TIER_COUNT
        + ["addTier", "create", "addTier"]
        + ["addTier"] * (len(PLANNED_INDEXES) - EXPIRE_AFTER_TIER_COUNT - 1)
        + ["delete"]
    )
    actual_sequence = [entry.get("operationId") for entry in entries]
    require(
        actual_sequence == expected_sequence,
        f"case {label} request sequence changed:\n  got  {actual_sequence}\n  want {expected_sequence}",
    )
    require(
        all(entry.get("responseStatus") != 409 for entry in entries),
        f"case {label} re-created a tier that already existed",
    )
    require(
        sum(1 for entry in entries if entry.get("responseStatus") == 401) == 1,
        f"case {label} must take exactly one 401",
    )

    index = 0
    check_token_create(entries[index], expected_credential, f"{label} initial create")
    index += 1

    # -- pagination ------------------------------------------------------
    expected_targets = [
        f"{BASE_PATH}/groups/applications?size={PAGE_SIZE}",
        f"{BASE_PATH}/groups/applications?size={PAGE_SIZE}&cursor=Mw%3D%3D",
        f"{BASE_PATH}/groups/applications?size={PAGE_SIZE}&cursor=Ng%3D%3D",
    ]
    for page, expected_target in enumerate(expected_targets):
        entry = entries[index]
        label_page = f"{label} listApplications page {page + 1}"
        require(entry.get("method") == "GET", f"{label_page} must be a GET")
        require(
            entry.get("rawTarget") == expected_target,
            f"{label_page} target changed:\n  got  {entry.get('rawTarget')}\n  want {expected_target}",
        )
        require(
            "modifiedAfter" not in entry.get("query", {}),
            f"{label_page} sent the unset modifiedAfter parameter",
        )
        if page == 0:
            require(
                "cursor" not in entry.get("query", {}),
                f"{label_page} sent a cursor before the appliance issued one",
            )
        check_common(entry)
        check_no_body(entry, label_page)
        check_authorized(entry, 0, label_page)
        require(entry.get("responseStatus") == 200, f"{label_page} did not succeed")
        index += 1

    # -- detail fetches --------------------------------------------------
    for position, application in enumerate(applications):
        entry = entries[index]
        label_detail = f"{label} getApplicationById #{position}"
        quoted = quote(application["entity_id"], safe="")
        expected_target = f"{BASE_PATH}/groups/applications/{quoted}"
        require(entry.get("method") == "GET", f"{label_detail} must be a GET")
        require(
            entry.get("rawTarget") == expected_target,
            f"{label_detail} target changed:\n  got  {entry.get('rawTarget')}\n  want {expected_target}",
        )
        require(
            entry.get("rawQuery") == "",
            f"{label_detail} sent unset optional query parameters",
        )
        check_common(entry)
        check_no_body(entry, label_detail)
        check_authorized(entry, 0, label_detail)
        index += 1

    # -- tier creation, with the refresh in the middle -------------------
    planned = [(position, applications[position]) for position in PLANNED_INDEXES]
    expected_bodies = [
        encode(project_tier_request(plan[application["name"]]))
        for _position, application in planned
    ]

    for offset in range(EXPIRE_AFTER_TIER_COUNT):
        position, application = planned[offset]
        entry = entries[index]
        label_tier = f"{label} addTier #{offset}"
        quoted = quote(application["entity_id"], safe="")
        require(
            entry.get("rawTarget") == f"{BASE_PATH}/groups/applications/{quoted}/tiers",
            f"{label_tier} target changed",
        )
        require(entry.get("rawQuery") == "", f"{label_tier} must send no query string")
        check_common(entry)
        check_authorized(entry, 0, label_tier)
        check_body(entry, expected_bodies[offset], label_tier)
        require(entry.get("responseStatus") == 201, f"{label_tier} did not create a tier")
        index += 1

    # The token lapses here. The rejected request is the one that must be
    # replayed - nothing before it may be sent again.
    position, application = planned[EXPIRE_AFTER_TIER_COUNT]
    quoted = quote(application["entity_id"], safe="")
    expected_target = f"{BASE_PATH}/groups/applications/{quoted}/tiers"
    rejected = entries[index]
    require(
        rejected.get("rawTarget") == expected_target
        and rejected.get("responseStatus") == 401,
        f"{label} the expired token must be observed on the 4th addTier",
    )
    check_authorized(rejected, 0, f"{label} rejected addTier")
    check_body(rejected, expected_bodies[EXPIRE_AFTER_TIER_COUNT], f"{label} rejected addTier")
    index += 1

    check_token_create(entries[index], expected_credential, f"{label} refresh create")
    index += 1

    replayed = entries[index]
    require(
        replayed.get("operationId") == "addTier"
        and replayed.get("rawTarget") == expected_target,
        f"{label} the refreshed token must replay the rejected addTier, not restart the run",
    )
    check_authorized(replayed, 1, f"{label} replayed addTier")
    check_body(replayed, expected_bodies[EXPIRE_AFTER_TIER_COUNT], f"{label} replayed addTier")
    require(
        replayed.get("body") == rejected.get("body"),
        f"{label} the replay must resend the identical body",
    )
    require(replayed.get("responseStatus") == 201, f"{label} the replay did not create the tier")
    index += 1

    for offset in range(EXPIRE_AFTER_TIER_COUNT + 1, len(planned)):
        position, application = planned[offset]
        entry = entries[index]
        label_tier = f"{label} addTier #{offset}"
        quoted = quote(application["entity_id"], safe="")
        require(
            entry.get("rawTarget") == f"{BASE_PATH}/groups/applications/{quoted}/tiers",
            f"{label_tier} target changed",
        )
        check_common(entry)
        check_authorized(entry, 1, label_tier)
        check_body(entry, expected_bodies[offset], label_tier)
        require(entry.get("responseStatus") == 201, f"{label_tier} did not create a tier")
        index += 1

    final = entries[index]
    require(final.get("operationId") == "delete", f"{label} must release its token")
    require(final.get("method") == "DELETE", f"{label} delete must be a DELETE")
    require(
        final.get("rawTarget") == f"{BASE_PATH}/auth/token",
        f"{label} delete target changed",
    )
    require(final.get("rawQuery") == "", f"{label} delete must send no query string")
    check_common(final)
    check_no_body(final, f"{label} delete")
    check_authorized(final, 1, f"{label} delete")
    require(final.get("responseStatus") == 204, f"{label} delete did not succeed")
    index += 1
    require(index == len(entries), f"case {label} sent unexpected trailing requests")

    # -- omission of unset optional members, read back off the wire -------
    tier_bodies = [
        entry["body"]
        for entry in entries
        if entry.get("operationId") == "addTier" and entry.get("responseStatus") == 201
    ]
    require(
        len(tier_bodies) == len(planned),
        f"{label} must commit exactly {len(planned)} tiers",
    )
    for offset, (_position, application) in enumerate(planned):
        supplied = plan[application["name"]]
        sent = json.loads(tier_bodies[offset])
        for member in TIER_REQUEST_ORDER:
            if member not in supplied or supplied[member] is None:
                require(
                    member not in sent,
                    f"{label} unset TierRequest member {member} was serialized",
                )
        require(
            list(sent) == [name for name in TIER_REQUEST_ORDER if name in sent],
            f"{label} TierRequest members must follow the specification order",
        )
    for entry in entries:
        if entry.get("operationId") != "create":
            continue
        sent_credential = json.loads(entry["body"])
        require(
            list(sent_credential)
            == [name for name in USER_CREDENTIAL_ORDER if name in sent_credential],
            f"{label} UserCredential members must follow the specification order",
        )
        if domain is None:
            require("domain" not in sent_credential, "an unset domain must be omitted")
        else:
            for member in DOMAIN_ORDER:
                if domain.get(member) is None:
                    require(
                        member not in sent_credential["domain"],
                        f"unset Domain member {member} was serialized",
                    )

    # -- report ----------------------------------------------------------
    report = result["report"]
    require(result.get("reportKeyOrder") == REPORT_KEYS, f"{label} report key order changed")
    require(
        report["base_url"] == appliance.base_url,
        f"{label} report base_url changed",
    )
    require(report["pages_fetched"] == 3, f"{label} pages_fetched changed")
    require(
        report["applications_listed"] == APPLICATION_COUNT,
        f"{label} applications_listed changed",
    )
    require(report["token_refreshes"] == 1, f"{label} token_refreshes must be 1")
    require(
        len(report["tiers_created"]) == len(PLANNED_INDEXES),
        f"{label} every planned tier must be reported exactly once",
    )
    for offset, (position, application) in enumerate(planned):
        record = report["tiers_created"][offset]
        require(list(record) == TIER_RECORD_KEYS, f"{label} tier record key order changed")
        require(
            record["application_entity_id"] == application["entity_id"]
            and record["application_name"] == application["name"]
            and record["tier_name"] == plan[application["name"]]["name"]
            and record["tier_entity_id"] == f"{scenario['tierIdPrefix']}{offset + 1}",
            f"{label} tier record #{offset} is wrong",
        )
    require(
        report["applications_without_plan"]
        == [
            applications[position]["name"]
            for position in range(APPLICATION_COUNT)
            if position not in PLANNED_INDEXES
        ],
        f"{label} applications_without_plan changed",
    )
    require(
        report["plan_entries_without_application"]
        == [f"aa-absent-{nonce}", f"zz-absent-{nonce}"],
        f"{label} plan_entries_without_application must be sorted, not plan order",
    )


def verify_unrecoverable_case(workdir: Path) -> None:
    """A token that is dead on arrival must stop the run after one refresh."""
    label = "always-expired"
    nonce = secrets.token_hex(4)
    scenario = build_scenario("always_expired", nonce)
    plan = build_plan(scenario, nonce)
    credentials = scenario["credentials"]

    appliance = Appliance(workdir, scenario, label)
    try:
        result = run_case(
            workdir,
            label,
            {
                "base_url": appliance.base_url,
                "username": credentials["username"],
                "password": credentials["password"],
                "plan": plan,
                "domain": None,
                "page_size": PAGE_SIZE,
            },
        )
        entries = appliance.entries()
    finally:
        appliance.stop()

    require(
        result.get("status") == "error",
        "a permanently rejected token must not report success",
    )
    require(
        result.get("isTokenRefreshError") is True,
        f"expected TokenRefreshError, got {result.get('errorType')}: {result.get('errorMessage')}",
    )
    require(
        result.get("isVcfOnError") is True,
        "TokenRefreshError must derive from VcfOnError",
    )
    sequence = [entry.get("operationId") for entry in entries]
    require(
        sequence == ["create", "listApplications", "create", "listApplications"],
        f"the refresh must be attempted once and then give up, got {sequence}",
    )
    require(
        [entry.get("responseStatus") for entry in entries] == [200, 401, 200, 401],
        "the unrecoverable run took an unexpected status sequence",
    )
    require(
        entries[1].get("presentedTokenIndex") == 0
        and entries[3].get("presentedTokenIndex") == 1,
        "the retry must present the freshly issued token",
    )
    require(
        all(entry.get("operationId") != "addTier" for entry in entries),
        "no tier may be created once authentication is unrecoverable",
    )


def verify_validation(workdir: Path) -> None:
    """Every explicitly validated argument must fail before authentication."""
    label = "validation"
    nonce = secrets.token_hex(4)
    scenario = build_scenario("expire_after_tiers", nonce)
    credentials = scenario["credentials"]
    application_name = scenario["applications"][0]["name"]
    valid_plan = {application_name: {"name": f"tier-{nonce}"}}

    appliance = Appliance(workdir, scenario, label)
    try:
        base_case = {
            "base_url": appliance.base_url,
            "username": credentials["username"],
            "password": credentials["password"],
            "plan": valid_plan,
            "domain": None,
            "page_size": PAGE_SIZE,
        }
        invalid_cases = [
            ("base-url", {"base_url": appliance.base_url + "/not-an-appliance-root"}),
            ("base-url-query", {"base_url": appliance.base_url + "?unexpected=1"}),
            ("username", {"username": ""}),
            ("username-type", {"username": 7}),
            ("password", {"password": ""}),
            ("password-type", {"password": None}),
            ("plan-type", {"plan": []}),
            ("plan-key", {"plan": {"": {"name": f"tier-{nonce}"}}}),
            ("plan-value", {"plan": {application_name: []}}),
            (
                "plan-member",
                {
                    "plan": {
                        application_name: {
                            "name": f"tier-{nonce}",
                            "outside_tier_request": True,
                        }
                    }
                },
            ),
            ("tier-name", {"plan": {application_name: {"name": None}}}),
            ("domain", {"domain": []}),
            ("page-size-zero", {"page_size": 0}),
            ("page-size-bool", {"page_size": True}),
            ("page-size-float", {"page_size": 1.5}),
        ]
        for case_label, overrides in invalid_cases:
            case = {**base_case, **overrides}
            result = run_case(workdir, f"{label}-{case_label}", case)
            require(
                result.get("status") == "error"
                and result.get("isVcfOnError") is True,
                f"invalid {case_label} must raise VcfOnError before authentication",
            )
        entries = appliance.entries()
    finally:
        appliance.stop()

    require(
        entries == [],
        "argument validation must finish before the first appliance request",
    )


def verify_api_error(workdir: Path) -> None:
    """A non-401 appliance failure must retain the contract error fields."""
    label = "api-error"
    nonce = secrets.token_hex(4)
    scenario = build_scenario("expire_after_tiers", nonce)
    code = 500000 + int(nonce[:3], 16)
    message = f"runtime-list-failure-{nonce}"
    scenario["forcedError"] = {
        "operationId": "listApplications",
        "status": 500,
        "code": code,
        "message": message,
    }
    credentials = scenario["credentials"]

    appliance = Appliance(workdir, scenario, label)
    try:
        result = run_case(
            workdir,
            label,
            {
                "base_url": appliance.base_url,
                "username": credentials["username"],
                "password": credentials["password"],
                "plan": build_plan(scenario, nonce),
                "domain": None,
                "page_size": PAGE_SIZE,
            },
        )
        entries = appliance.entries()
    finally:
        appliance.stop()

    require(result.get("status") == "error", "an HTTP 500 must not report success")
    require(result.get("isApiError") is True, "an HTTP 500 must raise ApiError")
    require(result.get("isVcfOnError") is True, "ApiError must derive from VcfOnError")
    require(
        result.get("errorStatus") == 500
        and result.get("errorCode") == code
        and result.get("apiMessage") == message,
        "ApiError must retain its status, code and message attributes",
    )
    require(
        [entry.get("operationId") for entry in entries]
        == ["create", "listApplications"],
        "the rollout must stop immediately after a non-success response",
    )
    require(
        [entry.get("responseStatus") for entry in entries] == [200, 500],
        "the ApiError case took an unexpected status sequence",
    )


def main() -> int:
    try:
        verify_contract()
        verify_solution_shape()
        with tempfile.TemporaryDirectory(prefix="vcf91-0290-") as raw:
            workdir = Path(raw)
            verify_rollout_case(workdir, "local-no-domain", None)
            verify_rollout_case(
                workdir,
                "local-domain",
                {"domain_type": "LOCAL", "value": None},
                trailing_slash=True,
            )
            verify_unrecoverable_case(workdir)
            verify_validation(workdir)
            verify_api_error(workdir)
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        VerificationError,
    ) as error:
        print(f"FAIL: {error}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
