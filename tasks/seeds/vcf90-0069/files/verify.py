#!/usr/bin/env python3
"""Acceptance harness for the opsgroups package.

Drives opsgroups against a contract-pinned loopback VCF Operations fixture and
asserts the exact wire shape it produced, using the fixture's request log.
Nothing here contacts a live VMware endpoint; the fixture binds 127.0.0.1 only.

Part of the protected harness. Run with: python3 verify.py
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mock_ops import (  # noqa: E402
    ID_TEMPLATE,
    MockOperations,
    VALID_AUTH_SOURCE,
    VALID_PASSWORD,
    VALID_USER,
)

FAILURES = []
CHECKS = [0]

EXPECTED_TAG = "9.0.0.0"
EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
EXPECTED_OPERATIONS = {
    "acquireToken",
    "getCustomGroups",
    "createCustomGroup",
    "modifyCustomGroup",
}
# The 9.1.0.0 tag ships the same file at this commit. Nothing may be pinned to it.
FORBIDDEN_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"

GROUPS_TARGET = "/suite-api/api/resources/groups"
ACQUIRE_TARGET = "/suite-api/api/auth/token/acquire"
TOKEN_PREFIX = "vRealizeOpsToken "

GROUP_NAME = "Production Linux VMs"
ADAPTER_KIND = "Container"
RESOURCE_KIND = "Environment"

RULES_80 = [{
    "resourceKindKey": {"resourceKind": "VirtualMachine", "adapterKind": "VMWARE"},
    "propertyConditionRules": [
        {"key": "summary|guest|fullName", "stringValue": "Linux",
         "compareOperator": "CONTAINS"},
    ],
    "statConditionRules": [
        {"key": "cpu|usage_average", "doubleValue": 80.0, "compareOperator": "GT"},
    ],
}]
RULES_65 = json.loads(json.dumps(RULES_80))
RULES_65[0]["statConditionRules"][0]["doubleValue"] = 65.0


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
    eq("contract pinned to the 9.0.0.0 commit sha", src["commitSha"], EXPECTED_SHA)
    eq("contract cites the vcf-operations spec path", src["specPath"], EXPECTED_SPEC_PATH)
    eq("contract names exactly the operations in scope",
       {o["operationId"] for o in contract["operations"]}, EXPECTED_OPERATIONS)
    eq("contract carries the specification's base path",
       contract["server"]["basePath"], "/suite-api")

    spec = sources["specification"]
    eq("official_sources records the tag", spec["repository_tag"], EXPECTED_TAG)
    eq("official_sources records the commit sha", spec["repository_commit_sha"],
       EXPECTED_SHA)
    eq("official_sources records the spec path", spec["spec_path"], EXPECTED_SPEC_PATH)
    eq("official_sources records every operationId",
       {o["operationId"] for o in sources["operations"]}, EXPECTED_OPERATIONS)
    check("every recorded operation carries its own spec path and sha",
          all(o.get("spec_path") == EXPECTED_SPEC_PATH
              and o.get("repository_commit_sha") == EXPECTED_SHA
              for o in sources["operations"]))
    check("nothing is pinned to the 9.1 revision of the same file",
          FORBIDDEN_SHA not in json.dumps(contract),
          "contract.json cites the 9.1.0.0 commit as its source")


# ---------------------------------------------------------------------------
# 2. The package is importable and uses nothing but the standard library
# ---------------------------------------------------------------------------
def load_package():
    section("package shape")
    try:
        import opsgroups
    except Exception as exc:  # pragma: no cover - reported as a failure
        check("opsgroups imports", False, "%s: %s" % (type(exc).__name__, exc))
        return None

    check("opsgroups imports", True)
    check("opsgroups is a package", hasattr(opsgroups, "__path__"),
          "opsgroups must be a package directory with __init__.py")
    for name in ("ensure_custom_group", "OpsError", "OpsAuthError", "OpsApiError",
                 "OpsConflictError"):
        check("opsgroups exports %s" % name, hasattr(opsgroups, name))

    if not hasattr(opsgroups, "__path__"):
        return opsgroups

    root = list(opsgroups.__path__)[0]
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    denied = None
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
                    if not mod or mod == "opsgroups":
                        continue
                    bad = (mod not in stdlib) if stdlib is not None else (mod in denied)
                    if bad:
                        offenders.append("%s imports %r"
                                         % (os.path.relpath(full, HERE), mod))
    check("package has python sources", sources_seen > 0)
    check("package imports only the standard library", not offenders,
          "\n         ".join(sorted(set(offenders))))
    return opsgroups


# ---------------------------------------------------------------------------
# 3. Wire-shape helpers
# ---------------------------------------------------------------------------
def by_operation(entries, operation_id):
    return [e for e in entries if e["operationId"] == operation_id]


def first_body(entries, operation_id):
    """The JSON body of the first call to an operation, or {} when it never came."""
    calls = by_operation(entries, operation_id)
    return (calls[0].get("body") or {}) if calls else {}


def check_conversation(prefix, entries, creates=0, modifies=0, lookups=1):
    """The shape every run shares: one login, one lookup, and the writes named."""
    unrouted = [e for e in entries if e["operationId"] is None]
    eq("%s: no request outside the pinned contract" % prefix,
       [(e["method"], e["target"]) for e in unrouted], [])

    acquires = by_operation(entries, "acquireToken")
    eq("%s: exactly one acquireToken" % prefix, len(acquires), 1)
    eq("%s: %d getCustomGroups" % (prefix, lookups),
       len(by_operation(entries, "getCustomGroups")), lookups)
    eq("%s: %d createCustomGroup" % (prefix, creates),
       len(by_operation(entries, "createCustomGroup")), creates)
    eq("%s: %d modifyCustomGroup" % (prefix, modifies),
       len(by_operation(entries, "modifyCustomGroup")), modifies)
    if not acquires:
        return None

    acquire = acquires[0]
    eq("%s: acquireToken is POST %s" % (prefix, ACQUIRE_TARGET),
       (acquire["method"], acquire["target"]), ("POST", ACQUIRE_TARGET))
    eq("%s: acquireToken is the first request" % prefix, entries[0]["seq"], acquire["seq"])
    check("%s: acquireToken carries no Authorization header" % prefix,
          "authorization" not in acquire["headers"],
          "it is the call that mints the token")

    token = None
    body = acquire.get("body")
    if isinstance(body, dict):
        eq("%s: acquireToken sends the supplied credentials" % prefix,
           (body.get("username"), body.get("password")), (VALID_USER, VALID_PASSWORD))

    for entry in entries:
        tag = "%s: %s" % (prefix, entry["operationId"])
        accept = entry["headers"].get("accept", "")
        offered = {p.split(";")[0].strip().lower() for p in accept.split(",")}
        check("%s asks for JSON explicitly" % tag,
              bool(offered & {"application/json"}),
              "both media types are on offer; got Accept: %r" % accept)
        if entry["raw_body"]:
            ctype = (entry["headers"].get("content-type") or "").split(";")[0].strip()
            eq("%s sends a JSON body" % tag, ctype.lower(), "application/json")
            check("%s sends a well-formed JSON body" % tag, entry["body_is_json"])

    for entry in entries:
        if entry["operationId"] == "acquireToken":
            continue
        tag = "%s: %s" % (prefix, entry["operationId"])
        auth = entry["headers"].get("authorization", "")
        check("%s carries the acquired token" % tag, auth.startswith(TOKEN_PREFIX),
              "expected an %r header; got %r" % (TOKEN_PREFIX, auth))
        if token is None:
            token = auth
        eq("%s reuses the one acquired token" % tag, auth, token)

    lookup = by_operation(entries, "getCustomGroups")
    for entry in lookup:
        eq("%s: getCustomGroups is GET %s with no query string" % (prefix, GROUPS_TARGET),
           (entry["method"], entry["target"]), ("GET", GROUPS_TARGET))
        eq("%s: getCustomGroups sends no body" % prefix, entry["raw_body"], "")
    for entry in by_operation(entries, "createCustomGroup"):
        eq("%s: createCustomGroup is POST %s" % (prefix, GROUPS_TARGET),
           (entry["method"], entry["target"]), ("POST", GROUPS_TARGET))
    for entry in by_operation(entries, "modifyCustomGroup"):
        eq("%s: modifyCustomGroup is PUT %s" % (prefix, GROUPS_TARGET),
           (entry["method"], entry["target"]), ("PUT", GROUPS_TARGET))
    return token


def check_desired_key(prefix, body, resource_kind=RESOURCE_KIND):
    eq("%s: resource-key carries only the three required members" % prefix,
       sorted(body.get("resourceKey", {})),
       ["adapterKindKey", "name", "resourceKindKey"])
    eq("%s: resource-key names the desired group" % prefix,
       (body.get("resourceKey", {}).get("name"),
        body.get("resourceKey", {}).get("adapterKindKey"),
        body.get("resourceKey", {}).get("resourceKindKey")),
       (GROUP_NAME, ADAPTER_KIND, resource_kind))


def run(opsgroups, mock, **kwargs):
    """One ensure_custom_group call against a freshly truncated request log."""
    mock.truncate_log()
    params = dict(name=GROUP_NAME, adapter_kind_key=ADAPTER_KIND,
                  resource_kind_key=RESOURCE_KIND)
    params.update(kwargs)
    return opsgroups.ensure_custom_group(
        mock.base_url, VALID_USER, VALID_PASSWORD, **params)


# ---------------------------------------------------------------------------
# 4. Scenarios
# ---------------------------------------------------------------------------
def scenario_create(opsgroups, mock):
    section("A. the group is absent, so it is created")
    mock.reset()
    result = run(opsgroups, mock, rules=RULES_80, auto_resolve_membership=True)
    entries = mock.requests()

    check_conversation("A", entries, creates=1)
    acquire_body = first_body(entries, "acquireToken")
    eq("A: acquireToken body carries no unset optional member",
       sorted(acquire_body), ["password", "username"])
    check("A: authSource is absent rather than empty", "authSource" not in acquire_body)
    eq("A: getCustomGroups precedes the create",
       [e["operationId"] for e in entries],
       ["acquireToken", "getCustomGroups", "createCustomGroup"])

    body = first_body(entries, "createCustomGroup")
    eq("A: create body member set",
       sorted(body), ["autoResolveMembership", "membershipDefinition", "resourceKey"])
    check("A: create sends no id; the appliance assigns it", "id" not in body)
    check_desired_key("A", body)
    eq("A: membershipDefinition carries only the rules that were asked for",
       sorted(body.get("membershipDefinition", {})), ["rules"])
    eq("A: the rules travel unchanged",
       body.get("membershipDefinition", {}).get("rules"), RULES_80)
    eq("A: autoResolveMembership is the requested true",
       body.get("autoResolveMembership"), True)

    eq("A: action", result.get("action"), "created")
    eq("A: groupId is the identifier the appliance assigned",
       result.get("groupId"), ID_TEMPLATE % 1)
    eq("A: one mutating call was made", result.get("writes"), 1)
    eq("A: result keys in order", list(result), ["action", "groupId", "group", "writes"])
    eq("A: group is the custom group the appliance reported",
       result.get("group"), mock.groups()[0])
    eq("A: the appliance holds exactly one group", len(mock.groups()), 1)


def scenario_retry(opsgroups, mock):
    section("B. the same call again must not duplicate the group")
    for attempt in (2, 3):
        result = run(opsgroups, mock, rules=RULES_80, auto_resolve_membership=True)
        entries = mock.requests()
        prefix = "B%d" % attempt
        check_conversation(prefix, entries, creates=0, modifies=0)
        eq("%s: the run reads and stops" % prefix,
           [e["operationId"] for e in entries], ["acquireToken", "getCustomGroups"])
        eq("%s: action" % prefix, result.get("action"), "unchanged")
        eq("%s: no mutating call was made" % prefix, result.get("writes"), 0)
        eq("%s: the same group is reported" % prefix,
           result.get("groupId"), ID_TEMPLATE % 1)
        eq("%s: group is the last representation reported by the appliance" % prefix,
           result.get("group"), mock.groups()[0])
        eq("%s: the appliance still holds exactly one group" % prefix,
           len(mock.groups()), 1)
    check("B: the echoed empty collections were not mistaken for drift",
          all(member in mock.groups()[0]["membershipDefinition"]
              for member in ("includedResources", "excludedResources",
                             "custom-group-properties")),
          "fixture invariant: the appliance echoes every membership member")


def scenario_update(opsgroups, mock):
    section("C. the definition drifted, so the existing group is modified")
    result = run(opsgroups, mock, rules=RULES_65, auto_resolve_membership=True)
    entries = mock.requests()

    check_conversation("C", entries, creates=0, modifies=1)
    eq("C: the run reads then writes once",
       [e["operationId"] for e in entries],
       ["acquireToken", "getCustomGroups", "modifyCustomGroup"])

    body = first_body(entries, "modifyCustomGroup")
    eq("C: modify body member set", sorted(body),
       ["autoResolveMembership", "id", "membershipDefinition", "resourceKey"])
    eq("C: modify addresses the group found by the lookup",
       body.get("id"), ID_TEMPLATE % 1)
    check_desired_key("C", body)
    eq("C: membershipDefinition still omits what was not asked for",
       sorted(body.get("membershipDefinition", {})), ["rules"])
    eq("C: the drifted rules go out", body.get("membershipDefinition", {}).get("rules"),
       RULES_65)
    check("C: no policy member is sent", "policy" not in body)
    check("C: no links member is sent", "links" not in body)

    eq("C: action", result.get("action"), "updated")
    eq("C: one mutating call was made", result.get("writes"), 1)
    eq("C: the group kept its identifier", result.get("groupId"), ID_TEMPLATE % 1)
    eq("C: group is the updated representation reported by the appliance",
       result.get("group"), mock.groups()[0])
    eq("C: the appliance still holds exactly one group", len(mock.groups()), 1)
    eq("C: the appliance now holds the drifted rules",
       mock.groups()[0]["membershipDefinition"]["rules"], RULES_65)

    result = run(opsgroups, mock, rules=RULES_65, auto_resolve_membership=True)
    entries = mock.requests()
    check_conversation("C-retry", entries, creates=0, modifies=0)
    eq("C-retry: action", result.get("action"), "unchanged")
    eq("C-retry: no mutating call was made", result.get("writes"), 0)
    eq("C-retry: the appliance still holds exactly one group", len(mock.groups()), 1)


def scenario_same_name_other_kind(opsgroups, mock):
    section("D. same-named groups under other resource keys are different groups")
    other_kind = {
        "resourceKey": {
            "name": GROUP_NAME,
            "adapterKindKey": ADAPTER_KIND,
            "resourceKindKey": "Department",
        },
        "autoResolveMembership": True,
        "membershipDefinition": {"rules": json.loads(json.dumps(RULES_80))},
    }
    other_adapter = json.loads(json.dumps(other_kind))
    other_adapter["resourceKey"]["adapterKindKey"] = "CloudFoundryAdapter"
    other_adapter["resourceKey"]["resourceKindKey"] = RESOURCE_KIND
    mock.reset([other_kind, other_adapter])
    before = mock.groups()

    result = run(opsgroups, mock, rules=RULES_80, auto_resolve_membership=True)
    entries = mock.requests()

    check_conversation("D", entries, creates=1, modifies=0)
    eq("D: neither same-named group under another key was written to",
       len(by_operation(entries, "modifyCustomGroup")), 0)
    body = first_body(entries, "createCustomGroup")
    check_desired_key("D", body)
    eq("D: action", result.get("action"), "created")
    eq("D: the new group got its own identifier", result.get("groupId"), ID_TEMPLATE % 3)
    eq("D: the appliance now holds all three groups", len(mock.groups()), 3)
    eq("D: both pre-existing groups are untouched", mock.groups()[:2], before)


def scenario_auth_source(opsgroups, mock):
    section("E. an auth source is sent only when one was supplied")
    mock.reset()
    result = run(opsgroups, mock, rules=RULES_80, auto_resolve_membership=True,
                 auth_source=VALID_AUTH_SOURCE)
    entries = mock.requests()
    check_conversation("E", entries, creates=1)
    body = first_body(entries, "acquireToken")
    eq("E: acquireToken body member set", sorted(body),
       ["authSource", "password", "username"])
    eq("E: the supplied auth source goes out", body.get("authSource"), VALID_AUTH_SOURCE)
    eq("E: action", result.get("action"), "created")


def scenario_empty_membership(opsgroups, mock):
    section("F. an empty membership definition is sent as {}, not as empty members")
    mock.reset()
    result = run(opsgroups, mock, rules=[], included_resources=[],
                 excluded_resources=[], auth_source="")
    entries = mock.requests()
    check_conversation("F", entries, creates=1)

    acquire_body = first_body(entries, "acquireToken")
    check("F: an empty auth source is omitted rather than sent as an empty string",
          "authSource" not in acquire_body)

    creates = by_operation(entries, "createCustomGroup")
    raw_create = creates[0]["raw_body"] if creates else ""
    body = first_body(entries, "createCustomGroup")
    eq("F: create body member set", sorted(body),
       ["membershipDefinition", "resourceKey"])
    check("F: autoResolveMembership is absent when it was never asked for",
          "autoResolveMembership" not in body)
    eq("F: membershipDefinition is required, so it is present and empty",
       body.get("membershipDefinition"), {})
    check("F: no member was sent as an empty collection",
          "[]" not in raw_create, raw_create)
    check("F: no member was sent as null", "null" not in raw_create, raw_create)
    eq("F: action", result.get("action"), "created")

    result = run(opsgroups, mock)
    entries = mock.requests()
    check_conversation("F-retry", entries, creates=0, modifies=0)
    eq("F-retry: the appliance's normalised echo is not drift",
       result.get("action"), "unchanged")
    eq("F-retry: no mutating call was made", result.get("writes"), 0)
    eq("F-retry: the appliance still holds exactly one group", len(mock.groups()), 1)


def scenario_resources(opsgroups, mock):
    section("G. resource collections are deduplicated, sorted and repeatable")
    mock.reset()
    supplied = [
        "3f3d5c58-0000-4000-8000-00000000000c",
        "1a1b2c3d-0000-4000-8000-00000000000a",
        "3f3d5c58-0000-4000-8000-00000000000c",
        "2b2c3d4e-0000-4000-8000-00000000000b",
    ]
    excluded = [
        "6c6d7e8f-0000-4000-8000-00000000000f",
        "4d4e5f60-0000-4000-8000-00000000000d",
        "6c6d7e8f-0000-4000-8000-00000000000f",
        "5e5f6071-0000-4000-8000-00000000000e",
    ]
    result = run(opsgroups, mock, included_resources=supplied,
                 excluded_resources=excluded,
                 auto_resolve_membership=False)
    entries = mock.requests()
    check_conversation("G", entries, creates=1)

    body = first_body(entries, "createCustomGroup")
    eq("G: create body member set", sorted(body),
       ["autoResolveMembership", "membershipDefinition", "resourceKey"])
    check("G: autoResolveMembership false is a value that was asked for, not an unset one",
          body.get("autoResolveMembership") is False, repr(body.get("autoResolveMembership")))
    eq("G: membershipDefinition carries both supplied resource collections",
       sorted(body.get("membershipDefinition", {})),
       ["excludedResources", "includedResources"])
    eq("G: includedResources is deduplicated and sorted",
       body.get("membershipDefinition", {}).get("includedResources"),
       sorted(set(supplied)))
    eq("G: excludedResources is deduplicated and sorted",
       body.get("membershipDefinition", {}).get("excludedResources"),
       sorted(set(excluded)))
    eq("G: action", result.get("action"), "created")

    result = run(opsgroups, mock, included_resources=list(reversed(supplied)),
                 excluded_resources=list(reversed(excluded)),
                 auto_resolve_membership=False)
    entries = mock.requests()
    check_conversation("G-retry", entries, creates=0, modifies=0)
    eq("G-retry: the same set in another order is not drift",
       result.get("action"), "unchanged")
    eq("G-retry: no mutating call was made", result.get("writes"), 0)
    eq("G-retry: the appliance still holds exactly one group", len(mock.groups()), 1)

    # A real appliance is free to return uniqueItems arrays in a different order
    # from the canonical request. Seed that reported ordering directly so this is
    # not merely another check that the client sorted both of its own inputs.
    reordered = {
        "resourceKey": {
            "name": GROUP_NAME,
            "adapterKindKey": ADAPTER_KIND,
            "resourceKindKey": RESOURCE_KIND,
        },
        "autoResolveMembership": False,
        "membershipDefinition": {
            "includedResources": list(reversed(sorted(set(supplied)))),
            "excludedResources": list(reversed(sorted(set(excluded)))),
        },
    }
    mock.reset([reordered])
    result = run(opsgroups, mock, included_resources=supplied,
                 excluded_resources=excluded, auto_resolve_membership=False)
    entries = mock.requests()
    check_conversation("G-reordered", entries, creates=0, modifies=0)
    eq("G-reordered: response ordering is not drift", result.get("action"),
       "unchanged")
    eq("G-reordered: no mutating call was made", result.get("writes"), 0)


def scenario_unspecified_auto(opsgroups, mock):
    section("G2. an unspecified auto-resolution setting preserves appliance state")
    existing = {
        "resourceKey": {
            "name": GROUP_NAME,
            "adapterKindKey": ADAPTER_KIND,
            "resourceKindKey": RESOURCE_KIND,
        },
        "autoResolveMembership": True,
        "membershipDefinition": {},
    }
    mock.reset([existing])
    result = run(opsgroups, mock)
    entries = mock.requests()
    check_conversation("G2", entries, creates=0, modifies=0)
    eq("G2: the appliance's true value is preserved", result.get("action"),
       "unchanged")
    check("G2: the returned group still reports auto resolution enabled",
          result.get("group", {}).get("autoResolveMembership") is True)


def scenario_duplicates(opsgroups, mock):
    section("H. duplicates already on the appliance are refused, not written to")
    twin = {
        "resourceKey": {
            "name": GROUP_NAME,
            "adapterKindKey": ADAPTER_KIND,
            "resourceKindKey": RESOURCE_KIND,
        },
        "autoResolveMembership": True,
        "membershipDefinition": {"rules": json.loads(json.dumps(RULES_80))},
    }
    mock.reset([twin, json.loads(json.dumps(twin))])

    raised = None
    try:
        run(opsgroups, mock, rules=RULES_65, auto_resolve_membership=True)
    except Exception as exc:
        raised = exc
    entries = mock.requests()

    check("H: a pre-existing duplicate raises OpsConflictError",
          isinstance(raised, getattr(opsgroups, "OpsConflictError", ())),
          "raised %r" % (raised,))
    check("H: OpsConflictError is an OpsError",
          isinstance(raised, getattr(opsgroups, "OpsError", ())))
    eq("H: the colliding identifiers are exposed",
       sorted(getattr(raised, "group_ids", None) or []),
       [ID_TEMPLATE % 1, ID_TEMPLATE % 2])
    token = check_conversation("H", entries, creates=0, modifies=0)
    if token:
        check("H: the acquired token does not leak into the error",
              token.split()[-1] not in str(raised), str(raised))
    eq("H: nothing was written", [e["operationId"] for e in entries],
       ["acquireToken", "getCustomGroups"])
    eq("H: the appliance still holds exactly the two duplicates", len(mock.groups()), 2)


def scenario_bad_credentials(opsgroups, mock):
    section("I. rejected credentials stop the run before any group is read")
    mock.reset()
    mock.truncate_log()
    raised = None
    try:
        opsgroups.ensure_custom_group(
            mock.base_url, VALID_USER, "not-the-password",
            name=GROUP_NAME, adapter_kind_key=ADAPTER_KIND,
            resource_kind_key=RESOURCE_KIND, rules=RULES_80)
    except Exception as exc:
        raised = exc
    entries = mock.requests()

    check("I: a 401 from acquireToken raises OpsAuthError",
          isinstance(raised, getattr(opsgroups, "OpsAuthError", ())),
          "raised %r" % (raised,))
    check("I: OpsAuthError is an OpsError",
          isinstance(raised, getattr(opsgroups, "OpsError", ())))
    eq("I: only the login was attempted",
       [e["operationId"] for e in entries], ["acquireToken"])
    check("I: the password does not leak into the error",
          "not-the-password" not in str(raised), str(raised))
    eq("I: the appliance holds no group", len(mock.groups()), 0)


def scenario_unrouted(opsgroups, mock):
    section("J. a target outside the pinned contract surfaces as OpsApiError")
    mock.reset()
    mock.truncate_log()
    raised = None
    try:
        opsgroups.ensure_custom_group(
            mock.base_url + "/absent", VALID_USER, VALID_PASSWORD,
            name=GROUP_NAME, adapter_kind_key=ADAPTER_KIND,
            resource_kind_key=RESOURCE_KIND, rules=RULES_80)
    except Exception as exc:
        raised = exc

    check("J: an unrouted target raises OpsApiError",
          isinstance(raised, getattr(opsgroups, "OpsApiError", ())),
          "raised %r" % (raised,))
    check("J: OpsApiError is an OpsError",
          isinstance(raised, getattr(opsgroups, "OpsError", ())))
    eq("J: the HTTP status is exposed", getattr(raised, "status", None), 404)
    eq("J: the fixture served nothing outside the contract",
       [e["operationId"] for e in mock.requests()], [None])
    eq("J: the appliance holds no group", len(mock.groups()), 0)


def scenario_error_secrecy(opsgroups, mock):
    section("K. appliance error details cannot make credentials leak")
    mock.reset()
    mock.truncate_log()
    leaked_password = "server-echoed-password"
    mock.fail_next("acquireToken", 401, "Rejected password: %s" % leaked_password)
    raised = None
    try:
        opsgroups.ensure_custom_group(
            mock.base_url, VALID_USER, leaked_password,
            name=GROUP_NAME, adapter_kind_key=ADAPTER_KIND,
            resource_kind_key=RESOURCE_KIND)
    except Exception as exc:
        raised = exc
    entries = mock.requests()
    check("K-password: the forced 401 raises OpsAuthError",
          isinstance(raised, getattr(opsgroups, "OpsAuthError", ())))
    eq("K-password: only acquireToken was attempted",
       [e["operationId"] for e in entries], ["acquireToken"])
    check("K-password: echoed error detail does not expose the password",
          leaked_password not in str(raised), str(raised))

    mock.reset()
    mock.truncate_log()
    mock.fail_next("getCustomGroups", 503,
                   "Rejected header: {authorization}")
    raised = None
    try:
        run(opsgroups, mock)
    except Exception as exc:
        raised = exc
    entries = mock.requests()
    token_header = check_conversation("K-token", entries, creates=0, modifies=0)
    check("K-token: the forced 503 raises OpsApiError",
          isinstance(raised, getattr(opsgroups, "OpsApiError", ())))
    eq("K-token: the HTTP status is exposed", getattr(raised, "status", None), 503)
    if token_header:
        check("K-token: echoed error detail does not expose the acquired token",
              token_header.split()[-1] not in str(raised), str(raised))
    eq("K-token: no group was written", len(mock.groups()), 0)


def main():
    check_contract()
    opsgroups = load_package()
    if opsgroups is None or not hasattr(opsgroups, "ensure_custom_group"):
        print("\nopsgroups.ensure_custom_group is unavailable; stopping.")
        print("\n%d check(s), %d failure(s)" % (CHECKS[0], len(FAILURES)))
        return 1

    log_path = os.path.join(tempfile.mkdtemp(prefix="opsgroups-verify-"), "requests.jsonl")
    with MockOperations(log_path) as mock:
        for scenario in (scenario_create, scenario_retry, scenario_update,
                         scenario_same_name_other_kind, scenario_auth_source,
                         scenario_empty_membership, scenario_resources,
                         scenario_unspecified_auto,
                         scenario_duplicates, scenario_bad_credentials,
                         scenario_unrouted, scenario_error_secrecy):
            try:
                scenario(opsgroups, mock)
            except Exception as exc:
                check("%s raised %s" % (scenario.__name__, type(exc).__name__), False,
                      "%s" % exc)

    print("\n%d check(s), %d failure(s)" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for name in FAILURES:
            print("  - %s" % name)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
