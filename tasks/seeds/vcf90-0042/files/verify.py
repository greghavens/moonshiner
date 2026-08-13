#!/usr/bin/env python3
"""Acceptance harness for the vcperm package.

Drives vcperm against a contract-pinned loopback vCenter fixture and asserts the
exact wire shape it produced, using the fixture's request log. Nothing here
contacts a live VMware endpoint; the fixture binds 127.0.0.1 only.

Part of the protected harness. Run with: python3 verify.py
"""

from __future__ import annotations

import ast
import base64
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mock_vcenter  # noqa: E402
from mock_vcenter import MockVcenter, VALID_USER, VALID_PASSWORD  # noqa: E402

FAILURES = []
CHECKS = [0]

EXPECTED_TAG = "9.0.0.0"
EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
EXPECTED_OPERATIONS = {
    "Cis.Session_create",
    "Cis.Session_delete",
    "Vcenter.Authorization.Permissions_list",
}

SESSION_HEADER = "vmware-api-session-id"

# Fixture permissions in the order the report must emit them. Derived from the
# ordering key: object type, object id, principal domain ('localos' when the
# specification's optional domain is absent), principal name, principal type,
# role, permission id.
ALL_IN_ORDER = [
    "perm-0005", "perm-0007", "perm-0004", "perm-0001",
    "perm-0006", "perm-0003",
    "perm-0010", "perm-0011", "perm-0008", "perm-0013", "perm-0012",
    "perm-0014", "perm-0002",
]

RECORD_KEYS = [
    "permission", "object_type", "object_id", "principal_type",
    "principal_name", "principal_domain", "role", "propagating",
]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s%s" % (label, ("\n         " + detail) if detail else ""))
        FAILURES.append(label)
    return bool(condition)


def eq(label, actual, expected):
    return check(label, actual == expected,
                 "expected: %r\n         actual:   %r" % (expected, actual))


def section(title):
    print("\n== %s ==" % title)


# ---------------------------------------------------------------------------
# 1. Provenance of the pinned contract
# ---------------------------------------------------------------------------
def check_contract():
    section("pinned contract provenance")
    with open(os.path.join(HERE, "docs", "contract.json"), encoding="utf-8") as fh:
        contract = json.load(fh)
    with open(os.path.join(HERE, "docs", "official_sources.json"), encoding="utf-8") as fh:
        sources = json.load(fh)

    src = contract["source"]
    eq("contract pinned to tag %s" % EXPECTED_TAG, src["tag"], EXPECTED_TAG)
    eq("contract pinned to commit sha", src["commitSha"], EXPECTED_SHA)
    eq("contract cites the vcenter automation spec path", src["specPath"], EXPECTED_SPEC_PATH)
    eq("contract carries the 9.0.0.0 api version", src["apiVersion"], "9.0.0.0")
    eq("contract names exactly the operations in scope",
       {o["operationId"] for o in contract["operations"]}, EXPECTED_OPERATIONS)

    spec = sources["specification"]
    eq("official_sources records the tag", spec["repository_tag"], EXPECTED_TAG)
    eq("official_sources records the commit sha", spec["repository_commit_sha"], EXPECTED_SHA)
    eq("official_sources records the spec path", spec["spec_path"], EXPECTED_SPEC_PATH)
    eq("official_sources records every operationId",
       {o["operationId"] for o in sources["operations"]}, EXPECTED_OPERATIONS)
    check("every recorded operation carries its own spec path and sha",
          all(o.get("spec_path") == EXPECTED_SPEC_PATH
              and o.get("repository_commit_sha") == EXPECTED_SHA
              for o in sources["operations"]))


# ---------------------------------------------------------------------------
# 2. The package is importable and uses nothing but the standard library
# ---------------------------------------------------------------------------
def load_package():
    section("package shape")
    try:
        import vcperm
    except Exception as exc:  # pragma: no cover - reported as a failure
        check("vcperm imports", False, "%s: %s" % (type(exc).__name__, exc))
        return None

    check("vcperm imports", True)
    check("vcperm is a package", hasattr(vcperm, "__path__"),
          "vcperm must be a package directory with __init__.py")
    for name in ("collect_permissions", "VcenterError", "VcenterAuthError", "VcenterApiError"):
        check("vcperm exports %s" % name, hasattr(vcperm, name))

    if not hasattr(vcperm, "__path__"):
        return vcperm

    root = os.path.realpath(list(vcperm.__path__)[0])
    expected_root = os.path.realpath(os.path.join(HERE, "vcperm"))
    if not check("vcperm comes from the workspace root", root == expected_root,
                 "expected %r, imported %r" % (expected_root, root)):
        return None
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    if not stdlib:  # interpreter older than 3.10; fall back to a denylist
        stdlib = None
        denied = {"requests", "httpx", "urllib3", "aiohttp", "httplib2",
                  "pycurl", "yaml", "pyvmomi", "pyVmomi", "vmware", "six"}
    offenders = []
    sources_seen = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            sources_seen += 1
            with open(full, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=full)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import inside the package
                        continue
                    mods = [(node.module or "").split(".")[0]]
                else:
                    continue
                for mod in mods:
                    if not mod or mod == "vcperm":
                        continue
                    bad = (mod not in stdlib) if stdlib is not None else (mod in denied)
                    if bad:
                        offenders.append("%s imports %r" % (os.path.relpath(full, HERE), mod))
    check("package has python sources", sources_seen > 0)
    check("package imports only the standard library", not offenders,
          "\n         ".join(sorted(set(offenders))))
    return vcperm


# ---------------------------------------------------------------------------
# 3. Wire-shape helpers
# ---------------------------------------------------------------------------
def split_log(entries):
    """Return (session_create, [list calls], session_delete, unknown targets)."""
    creates = [e for e in entries if e["operationId"] == "Cis.Session_create"]
    lists = [e for e in entries if e["operationId"] == "Vcenter.Authorization.Permissions_list"]
    deletes = [e for e in entries if e["operationId"] == "Cis.Session_delete"]
    unknown = [e for e in entries if e["operationId"] is None]
    return creates, lists, deletes, unknown


def check_session_lifecycle(prefix, entries, expect_delete=True):
    creates, lists, deletes, unknown = split_log(entries)
    eq("%s: no request outside the pinned contract" % prefix,
       [(e["method"], e["target"]) for e in unknown], [])
    eq("%s: exactly one Cis.Session_create" % prefix, len(creates), 1)
    if not creates:
        return None
    create = creates[0]
    eq("%s: session create is POST /api/session" % prefix,
       (create["method"], create["target"]), ("POST", "/api/session"))
    eq("%s: session create is the first request" % prefix, entries[0]["seq"], create["seq"])

    auth = create["headers"].get("authorization", "")
    check("%s: session create uses the basic_auth scheme" % prefix, auth.startswith("Basic "))
    if auth.startswith("Basic "):
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        eq("%s: basic credentials are the supplied ones" % prefix,
           decoded, "%s:%s" % (VALID_USER, VALID_PASSWORD))
    check("%s: session create sends no request body" % prefix, create["raw_body"] == "",
          "spec declares no requestBody for Cis.Session_create; got %r" % create["raw_body"])
    check("%s: session create carries no %s header" % (prefix, SESSION_HEADER),
          SESSION_HEADER not in create["headers"])

    token = None
    if lists:
        token = lists[0]["headers"].get(SESSION_HEADER)
    if expect_delete:
        eq("%s: exactly one Cis.Session_delete" % prefix, len(deletes), 1)
        if deletes:
            delete = deletes[0]
            eq("%s: session delete is DELETE /api/session" % prefix,
               (delete["method"], delete["target"]), ("DELETE", "/api/session"))
            eq("%s: session delete is the last request" % prefix,
               entries[-1]["seq"], delete["seq"])
            eq("%s: session delete reuses the issued token" % prefix,
               delete["headers"].get(SESSION_HEADER), token)
            check("%s: session delete carries no Authorization header" % prefix,
                  "authorization" not in delete["headers"])
    else:
        eq("%s: no Cis.Session_delete" % prefix, len(deletes), 0)
    return token


def check_list_calls(prefix, lists, token, expected_bodies, expected_offsets,
                     empty_marker_calls=()):
    eq("%s: %d Permissions_list call(s)" % (prefix, len(expected_bodies)),
       len(lists), len(expected_bodies))
    for idx, entry in enumerate(lists):
        tag = "%s: list call %d" % (prefix, idx + 1)
        eq("%s target" % tag, (entry["method"], entry["target"]),
           ("POST", "/api/vcenter/authorization/permissions?action=list"))
        eq("%s uses the api_key_auth token" % tag,
           entry["headers"].get(SESSION_HEADER), token)
        check("%s carries no Authorization header" % tag,
              "authorization" not in entry["headers"],
              "the contract puts this operation under api_key_auth only")
        ctype = entry["headers"].get("content-type", "")
        check("%s declares a JSON content type" % tag,
              ctype.split(";")[0].strip().lower() == "application/json", "got %r" % ctype)
        check("%s sent valid JSON" % tag, entry["body_is_json"])

        if idx >= len(expected_bodies):
            continue
        expected = expected_bodies[idx]
        actual = entry["body"]
        if idx == 0:
            eq("%s body" % tag, actual, expected)
            if expected == {}:
                eq("%s body is exactly {} on the wire" % tag,
                   entry["raw_body"], "{}")
        else:
            # The marker is opaque and server-issued, so compare the rest
            # exactly and then decode the cursor.
            observed = json.loads(json.dumps(actual)) if isinstance(actual, dict) else actual
            marker = None
            if isinstance(observed, dict) and isinstance(observed.get("iterate"), dict):
                marker = observed["iterate"].pop("marker", None)
            eq("%s body apart from the marker" % tag, observed, expected)
            marker_is_valid = (isinstance(marker, str)
                               and (marker != "" or idx in empty_marker_calls))
            check("%s carries a server-issued marker" % tag, marker_is_valid)
            if isinstance(marker, str) and marker and idx not in empty_marker_calls:
                try:
                    offset, _spec = mock_vcenter._decode_marker(marker)
                except Exception as exc:
                    check("%s marker is a server-issued cursor" % tag, False, str(exc))
                else:
                    eq("%s resumes at the documented offset" % tag,
                       offset, expected_offsets[idx])


def records_for(ids):
    """The expected report records, in the order given."""
    by_id = {item["permission"]: item for item in mock_vcenter.PERMISSIONS}
    out = []
    for pid in ids:
        info = by_id[pid]["info"]
        out.append({
            "permission": pid,
            "object_type": info["object"]["type"],
            "object_id": info["object"]["id"],
            "principal_type": info["principal"]["type"],
            "principal_name": info["principal"]["name"],
            "principal_domain": info["principal"].get("domain") or "localos",
            "role": info["role"],
            "propagating": info["propagating"],
        })
    return out


def check_result(prefix, result, expected_ids, expected_pages):
    check("%s: result is a dict" % prefix, isinstance(result, dict),
          "got %r" % type(result).__name__)
    if not isinstance(result, dict):
        return
    eq("%s: result keys in the documented order" % prefix,
       list(result.keys()), ["permissions", "count", "pages"])
    permissions = result.get("permissions")
    check("%s: permissions is a list" % prefix, isinstance(permissions, list))
    if not isinstance(permissions, list):
        return
    eq("%s: collection retrieved completely and in a stable order" % prefix,
       [p.get("permission") for p in permissions if isinstance(p, dict)], expected_ids)
    eq("%s: records normalised" % prefix, permissions, records_for(expected_ids))
    for idx, permission in enumerate(permissions):
        if isinstance(permission, dict):
            eq("%s: record %d keys in the documented order" % (prefix, idx + 1),
               list(permission.keys()), RECORD_KEYS)
    eq("%s: count matches" % prefix, result.get("count"), len(expected_ids))
    eq("%s: pages counts only Permissions_list calls" % prefix,
       result.get("pages"), expected_pages)


# ---------------------------------------------------------------------------
# 4. Scenarios
# ---------------------------------------------------------------------------
def run(vcperm, mock, fixture_behavior=None, **kwargs):
    mock.reset_behavior()
    if fixture_behavior:
        mock.configure(**fixture_behavior)
    mock.truncate_log()
    result = vcperm.collect_permissions(
        mock.base_url, VALID_USER, VALID_PASSWORD, **kwargs)
    return result, mock.requests()


def scenario_unfiltered_defaults(vcperm, mock):
    section("scenario A: no filter, no page size")
    result, entries = run(vcperm, mock)
    token = check_session_lifecycle("A", entries)
    _, lists, _, _ = split_log(entries)
    # Nothing was asked for, so neither optional body member may appear at all.
    check_list_calls("A", lists, token, [{}], [None])
    check_result("A", result, ALL_IN_ORDER, 1)


def scenario_paged(vcperm, mock):
    section("scenario B: unfiltered, page_size=2, seven pages")
    result, entries = run(vcperm, mock, page_size=2)
    token = check_session_lifecycle("B", entries)
    _, lists, _, _ = split_log(entries)
    first = {"iterate": {"page_size": 2}}
    rest = {"iterate": {"page_size": 2}}
    check_list_calls("B", lists, token,
                     [first] + [rest] * 6, [None, 2, 4, 6, 8, 10, 12])
    check_result("B", result, ALL_IN_ORDER, 7)


def scenario_filtered(vcperm, mock):
    section("scenario C: role filter, page_size=2, filter only on the first call")
    result, entries = run(vcperm, mock,
                          roles=["ReadOnly", "Admin", "ReadOnly"], page_size=2)
    token = check_session_lifecycle("C", entries)
    _, lists, _, _ = split_log(entries)
    first = {"filter": {"roles": ["Admin", "ReadOnly"]}, "iterate": {"page_size": 2}}
    rest = {"iterate": {"page_size": 2}}
    check_list_calls("C", lists, token, [first, rest, rest], [None, 2, 4])
    check_result("C", result,
                 ["perm-0005", "perm-0004", "perm-0001", "perm-0006", "perm-0003"], 3)


def scenario_false_is_not_unset(vcperm, mock):
    section("scenario D: is_propagating=False is sent, not omitted")
    result, entries = run(vcperm, mock, is_propagating=False)
    token = check_session_lifecycle("D", entries)
    _, lists, _, _ = split_log(entries)
    check_list_calls("D", lists, token, [{"filter": {"is_propagating": False}}], [None])
    check_result("D", result, ["perm-0005", "perm-0007", "perm-0002"], 1)


def scenario_bad_credentials(vcperm, mock):
    section("scenario E: rejected credentials")
    mock.reset_behavior()
    mock.configure(echo_auth_failure=True)
    mock.truncate_log()
    raised = None
    try:
        vcperm.collect_permissions(mock.base_url, VALID_USER, "wrong-password")
    except Exception as exc:
        raised = exc
    check("E: rejected credentials raise VcenterAuthError",
          isinstance(raised, getattr(vcperm, "VcenterAuthError", ())),
          "raised %r" % (raised,))
    if raised is not None:
        check("E: VcenterAuthError is a VcenterError",
              isinstance(raised, getattr(vcperm, "VcenterError", ())))
        check("E: the password does not leak into the error",
              "wrong-password" not in str(raised), str(raised))
    entries = mock.requests()
    _, lists, deletes, _ = split_log(entries)
    eq("E: no Permissions_list call after a failed login", len(lists), 0)
    eq("E: no Cis.Session_delete without a session", len(deletes), 0)


def scenario_api_error(vcperm, mock):
    section("scenario F: non-401 failure surfaces as VcenterApiError")
    mock.reset_behavior()
    mock.truncate_log()
    raised = None
    try:
        vcperm.collect_permissions(mock.base_url + "/absent", VALID_USER, VALID_PASSWORD)
    except Exception as exc:
        raised = exc
    check("F: an unrouted target raises VcenterApiError",
          isinstance(raised, getattr(vcperm, "VcenterApiError", ())),
          "raised %r" % (raised,))
    if raised is not None:
        check("F: VcenterApiError is a VcenterError",
              isinstance(raised, getattr(vcperm, "VcenterError", ())))
        eq("F: the HTTP status is exposed", getattr(raised, "status", None), 404)
        check("F: the password does not leak into the error",
              VALID_PASSWORD not in str(raised), str(raised))


def scenario_empty_roles(vcperm, mock):
    section("scenario G: an explicitly empty roles filter is not omitted")
    result, entries = run(vcperm, mock, roles=[])
    token = check_session_lifecycle("G", entries)
    _, lists, _, _ = split_log(entries)
    check_list_calls("G", lists, token, [{"filter": {"roles": []}}], [None])
    check_result("G", result, [], 1)


def scenario_empty_marker(vcperm, mock):
    section("scenario H: an empty string marker is still a marker")
    result, entries = run(
        vcperm, mock, fixture_behavior={"empty_first_marker": True}, page_size=2)
    token = check_session_lifecycle("H", entries)
    _, lists, _, _ = split_log(entries)
    first = {"iterate": {"page_size": 2}}
    rest = {"iterate": {"page_size": 2}}
    check_list_calls("H", lists, token,
                     [first] + [rest] * 6, [None, 2, 4, 6, 8, 10, 12],
                     empty_marker_calls={1})
    check_result("H", result, ALL_IN_ORDER, 7)


def scenario_null_marker(vcperm, mock):
    section("scenario I: an explicit null marker ends the collection")
    result, entries = run(
        vcperm, mock, fixture_behavior={"null_final_marker": True})
    token = check_session_lifecycle("I", entries)
    _, lists, _, _ = split_log(entries)
    check_list_calls("I", lists, token, [{}], [None])
    check_result("I", result, ALL_IN_ORDER, 1)


def scenario_logout_failure(vcperm, mock):
    section("scenario J: a failed logout does not mask a successful result")
    result, entries = run(vcperm, mock, fixture_behavior={"fail_logout": True})
    token = check_session_lifecycle("J", entries)
    _, lists, _, _ = split_log(entries)
    check_list_calls("J", lists, token, [{}], [None])
    check_result("J", result, ALL_IN_ORDER, 1)


def scenario_token_redaction(vcperm, mock):
    section("scenario K: an API error cannot leak the session token")
    mock.reset_behavior()
    mock.configure(fail_list_with_token=True)
    mock.truncate_log()
    raised = None
    try:
        vcperm.collect_permissions(mock.base_url, VALID_USER, VALID_PASSWORD)
    except Exception as exc:
        raised = exc
    check("K: list failure raises VcenterApiError",
          isinstance(raised, getattr(vcperm, "VcenterApiError", ())),
          "raised %r" % (raised,))
    if raised is not None:
        check("K: VcenterApiError is a VcenterError",
              isinstance(raised, getattr(vcperm, "VcenterError", ())))
        eq("K: the HTTP status is exposed", getattr(raised, "status", None), 500)
    entries = mock.requests()
    creates, lists, _deletes, unknown = split_log(entries)
    eq("K: no request outside the pinned contract",
       [(e["method"], e["target"]) for e in unknown], [])
    eq("K: exactly one session create", len(creates), 1)
    eq("K: exactly one failed list call", len(lists), 1)
    token = lists[0]["headers"].get(SESSION_HEADER) if lists else None
    check("K: the fixture issued a token for the redaction check",
          isinstance(token, str) and token != "")
    if raised is not None and token:
        check("K: the session token does not leak into the error",
              token not in str(raised), str(raised))


def main():
    check_contract()
    vcperm = load_package()
    if vcperm is None or not hasattr(vcperm, "collect_permissions"):
        print("\nvcperm.collect_permissions is unavailable; stopping.")
        print("\n%d check(s), %d failure(s)" % (CHECKS[0], len(FAILURES)))
        return 1

    log_path = os.path.join(tempfile.mkdtemp(prefix="vcperm-verify-"), "requests.jsonl")
    with MockVcenter(log_path) as mock:
        for scenario in (scenario_unfiltered_defaults, scenario_paged,
                         scenario_filtered, scenario_false_is_not_unset,
                         scenario_bad_credentials, scenario_api_error,
                         scenario_empty_roles, scenario_empty_marker,
                         scenario_null_marker, scenario_logout_failure,
                         scenario_token_redaction):
            try:
                scenario(vcperm, mock)
            except Exception as exc:
                check("%s raised %s" % (scenario.__name__, type(exc).__name__), False, str(exc))

    print("\n%d check(s), %d failure(s)" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for name in FAILURES:
            print("  - %s" % name)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
