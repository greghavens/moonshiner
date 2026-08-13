#!/usr/bin/env python3
"""Acceptance checks for vcfops_rotate.

Read-only. Do not edit anything in this directory.

Run from the repository root:

    python3 verification/verify.py

Exits 0 when every check passes, 1 otherwise. No network access is used or
permitted: a loopback guard is installed before the package is imported, so any
attempt to open a socket to a non-loopback address fails the run.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DOCS = ROOT / "docs"
CONTRACT_PATH = DOCS / "contract.json"
SOURCES_PATH = DOCS / "official_sources.json"

SPEC_REPO = "vmware/vcf-api-specs"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
SPEC_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_FILE_SHA256 = "8179d8c128d9b3a81ed89d12ab958dc3c43eb664d4ca999e3b19d3e70efc14af"
SPEC_TITLE = "VMware Cloud Foundation Operations API"
SPEC_VERSION = "9.1.0.0"
BASE_PATH = "/suite-api"
AUTH_HEADER = "Authorization"
AUTH_TEMPLATE = "vRealizeOpsToken {token}"

# (method, path) exactly as they appear in the OpenAPI document.
EXPECTED_OPS = {
    "acquireToken": ("POST", "/api/auth/token/acquire"),
    "releaseToken": ("POST", "/api/auth/token/release"),
    "getCredentials": ("GET", "/api/credentials"),
    "partialUpdateCredential": ("PATCH", "/api/credentials"),
    "getAdapterInstancesUsingCredential": ("GET", "/api/credentials/{id}/adapters"),
}

# sha256 of each operation's ``summary`` from the specification, whitespace
# stripped. Stored hashed so this file cannot be used as a substitute for
# reading the specification.
EXPECTED_SUMMARY_SHA256 = {
    "acquireToken": "d40d016996549fc8e834fddb12442593981953a6c71b8a2503c428540bf3103c",
    "releaseToken": "dff54197adde914157bb3acdac0ce77cd38dde74c76746667a4f892ae6bb22bb",
    "getCredentials": "cefa6bea5d473efd530a04a8dec481c1bbe1a05adba4aa87a64b369d74b57bee",
    "partialUpdateCredential": "81d9706f473bcc2276cef0d4f507342f3bea83be6b4365e6606f3a80bab9fd40",
    "getAdapterInstancesUsingCredential": (
        "8ec2c1f546069f20d6387afb2614e039ddcdd37e4427057085c97d8b46516dc3"
    ),
}

EXPECTED_BODY_FIELDS = {
    "acquireToken": (["password", "username"], ["authSource"]),
    "partialUpdateCredential": (
        ["adapterKindKey", "credentialKindKey", "name"],
        ["editable", "fields", "id"],
    ),
}

EXPECTED_QUERY = {
    "getCredentials": {"adapterKind", "id"},
}

EXPECTED_PATH_PARAMS = {
    "getAdapterInstancesUsingCredential": ["id"],
}

PLACEHOLDER_SHAS = {
    "0" * 40,
    "f" * 40,
    "1234567890abcdef1234567890abcdef12345678",
    "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
}

CRED_ID = "6f455a29-3330-47b6-9128-a608bca9d2c7"
OTHER_CRED_ID = "0b1c2d3e-4f50-4617-8829-aa0bb1cc2dd3"
USER = "svc-ops-collector"
OLD_SECRET = "old-secret-1a"
NEW_SECRET = "new-secret-2b"

WAIT = 10.0


# --------------------------------------------------------------------------
# result plumbing
# --------------------------------------------------------------------------

class Failure(Exception):
    pass


RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail and not ok:
        line += f"\n         {detail}"
    print(line, flush=True)


def check(name: str, fn) -> bool:
    try:
        fn()
    except Failure as exc:
        record(name, False, str(exc))
        return False
    except Exception:
        record(name, False, "unexpected error:\n" + traceback.format_exc(limit=6))
        return False
    record(name, True)
    return True


def require(cond, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def eq(actual, expected, what: str) -> None:
    if actual != expected:
        raise Failure(f"{what}: expected {expected!r}, got {actual!r}")


def load_json(path: Path, what: str):
    if not path.exists():
        raise Failure(f"{what} is missing (expected at {path.relative_to(ROOT)})")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Failure(f"{what} is not valid JSON: {exc}") from None


# --------------------------------------------------------------------------
# loopback guard
# --------------------------------------------------------------------------

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "ip6-localhost"}


def install_loopback_guard() -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection

    def host_of(address):
        if isinstance(address, tuple) and address:
            return str(address[0])
        return None

    def guard(address):
        host = host_of(address)
        if host is not None and host not in _LOOPBACK:
            raise Failure(
                f"blocked outbound connection to non-loopback address {host!r}; "
                "the client and mock must only talk to 127.0.0.1"
            )

    def connect(self, address):
        guard(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        guard(address)
        return real_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):
        guard(address)
        return real_create(address, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = create_connection


# --------------------------------------------------------------------------
# static checks
# --------------------------------------------------------------------------

def check_sources() -> None:
    doc = load_json(SOURCES_PATH, "docs/official_sources.json")
    require(isinstance(doc, dict), "docs/official_sources.json must be a JSON object")

    for key in ("repository", "license", "spec_path", "commit_sha", "raw_url",
                "file_sha256", "operations"):
        require(key in doc, f"docs/official_sources.json is missing key {key!r}")

    eq(doc["repository"], SPEC_REPO, "repository")
    eq(doc["license"], "Apache-2.0", "license")
    eq(doc["spec_path"], SPEC_PATH, "spec_path")

    sha = str(doc["commit_sha"]).strip()
    require(
        re.fullmatch(r"[0-9a-f]{40}", sha) is not None,
        f"commit_sha must be a 40-character lowercase hex git sha, got {sha!r}",
    )
    require(
        sha not in PLACEHOLDER_SHAS,
        "commit_sha is a placeholder value; record the sha of the revision the "
        "contract was actually derived from",
    )
    eq(sha, SPEC_COMMIT, "commit_sha")

    raw = str(doc["raw_url"])
    expected_raw = f"https://raw.githubusercontent.com/{SPEC_REPO}/{sha}/{SPEC_PATH}"
    eq(raw, expected_raw, "raw_url")

    file_sha = str(doc["file_sha256"]).strip()
    require(
        re.fullmatch(r"[0-9a-f]{64}", file_sha) is not None,
        f"file_sha256 must be 64 lowercase hex characters, got {file_sha!r}",
    )
    eq(file_sha, SPEC_FILE_SHA256, "file_sha256")

    ops = doc["operations"]
    require(isinstance(ops, list), "operations must be a list")
    by_id = {}
    for entry in ops:
        require(isinstance(entry, dict), "each operations entry must be an object")
        require("operation_id" in entry, "operations entry is missing 'operation_id'")
        require(
            entry["operation_id"] not in by_id,
            f"operations contains duplicate entry for {entry['operation_id']!r}",
        )
        by_id[entry["operation_id"]] = entry

    eq(len(ops), len(EXPECTED_OPS), "number of operations entries")
    eq(sorted(by_id), sorted(EXPECTED_OPS), "operations recorded in official_sources.json")

    for op_id, (method, path) in sorted(EXPECTED_OPS.items()):
        entry = by_id[op_id]
        for key in ("method", "path", "spec_summary"):
            require(key in entry, f"operations[{op_id}] is missing {key!r}")
        eq(str(entry["method"]).upper(), method, f"operations[{op_id}].method")
        eq(entry["path"], path, f"operations[{op_id}].path")
        summary = str(entry["spec_summary"]).strip()
        digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
        require(
            digest == EXPECTED_SUMMARY_SHA256[op_id],
            f"operations[{op_id}].spec_summary does not match the 'summary' field "
            f"for that operation in the specification (got {summary!r})",
        )


def contract_op(contract, op_id):
    ops = contract.get("operations")
    require(isinstance(ops, dict), "contract 'operations' must be a JSON object")
    require(op_id in ops, f"contract is missing operation {op_id!r}")
    entry = ops[op_id]
    require(isinstance(entry, dict), f"contract operation {op_id!r} must be an object")
    return entry


def check_contract() -> None:
    contract = load_json(CONTRACT_PATH, "docs/contract.json")
    require(isinstance(contract, dict), "docs/contract.json must be a JSON object")

    eq(contract.get("base_path"), BASE_PATH, "contract base_path")

    spec = contract.get("spec")
    require(isinstance(spec, dict), "contract must carry a 'spec' object")
    eq(spec.get("title"), SPEC_TITLE, "contract spec.title")
    eq(str(spec.get("version")), SPEC_VERSION, "contract spec.version")

    auth = contract.get("auth")
    require(isinstance(auth, dict), "contract must carry an 'auth' object")
    eq(auth.get("header"), AUTH_HEADER, "contract auth.header")
    eq(auth.get("value_template"), AUTH_TEMPLATE, "contract auth.value_template")

    ops = contract.get("operations")
    require(isinstance(ops, dict), "contract 'operations' must be a JSON object")
    eq(
        sorted(ops),
        sorted(EXPECTED_OPS),
        "contract must name exactly the operations it uses and no others",
    )

    for op_id, (method, path) in sorted(EXPECTED_OPS.items()):
        entry = contract_op(contract, op_id)
        eq(str(entry.get("method")).upper(), method, f"contract {op_id}.method")
        eq(entry.get("path"), path, f"contract {op_id}.path")

        expected_path_params = EXPECTED_PATH_PARAMS.get(op_id, [])
        eq(
            list(entry.get("path_params", [])),
            expected_path_params,
            f"contract {op_id}.path_params",
        )

        qs = entry.get("query_params", [])
        require(isinstance(qs, list), f"contract {op_id}.query_params must be a list")
        names = {q.get("name") for q in qs}
        eq(names, EXPECTED_QUERY.get(op_id, set()), f"contract {op_id} query parameter names")
        for q in qs:
            eq(q.get("required"), False, f"contract {op_id}.query_params[{q.get('name')}].required")
            eq(q.get("type"), "array", f"contract {op_id}.query_params[{q.get('name')}].type")
            eq(q.get("style"), "form", f"contract {op_id}.query_params[{q.get('name')}].style")
            eq(q.get("explode"), True, f"contract {op_id}.query_params[{q.get('name')}].explode")

        body = entry.get("request_body")
        if op_id in EXPECTED_BODY_FIELDS:
            req_fields, opt_fields = EXPECTED_BODY_FIELDS[op_id]
            require(isinstance(body, dict), f"contract {op_id} must declare a request_body")
            eq(
                body.get("content_type"),
                "application/json",
                f"contract {op_id}.request_body.content_type",
            )
            eq(
                sorted(body.get("required_fields", [])),
                req_fields,
                f"contract {op_id}.request_body.required_fields",
            )
            eq(
                sorted(body.get("optional_fields", [])),
                opt_fields,
                f"contract {op_id}.request_body.optional_fields",
            )
        else:
            require(
                body is None,
                f"contract {op_id} must declare request_body: null (the specification "
                f"defines no request body for it)",
            )

        eq(
            entry.get("requires_auth"),
            op_id != "acquireToken",
            f"contract {op_id}.requires_auth",
        )
        eq(entry.get("success_status"), 200, f"contract {op_id}.success_status")


def iter_py_files():
    skip = {"verification", "__pycache__", ".git"}
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if any(part in skip for part in rel.parts):
            continue
        yield path


def check_stdlib_only() -> None:
    stdlib = set(sys.stdlib_module_names)
    local = {"vcfops_rotate"}
    offenders = []
    seen_any = False
    for path in iter_py_files():
        seen_any = True
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                mods = [(node.module or "").split(".")[0]]
            else:
                continue
            for mod in mods:
                if not mod or mod in stdlib or mod in local:
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} imports {mod!r}")
    require(seen_any, "no Python sources found outside verification/")
    require(
        not offenders,
        "third-party imports are not allowed (standard library only):\n         "
        + "\n         ".join(sorted(set(offenders))),
    )


# --------------------------------------------------------------------------
# dynamic helpers
# --------------------------------------------------------------------------

def entries_for(mock, op_id):
    return [e for e in mock.entries() if e.get("operation_id") == op_id]


def last_entry(mock, op_id):
    got = entries_for(mock, op_id)
    require(got, f"the mock recorded no {op_id} request")
    return got[-1]


def wait_for(predicate, timeout=WAIT, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise Failure(f"timed out after {timeout:.0f}s waiting for {what}")


def header(entry, name):
    headers = entry.get("headers") or {}
    return headers.get(name.lower())


_NO_BODY = object()


def raw_request(url, method="GET", token=None, body=_NO_BODY):
    data = None if body is _NO_BODY else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if body is not _NO_BODY:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header(AUTH_HEADER, AUTH_TEMPLATE.format(token=token))
    try:
        with urllib.request.urlopen(req, timeout=WAIT) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


# --------------------------------------------------------------------------
# dynamic checks
# --------------------------------------------------------------------------

def run_dynamic() -> int:
    sys.path.insert(0, str(SRC))
    try:
        from vcfops_rotate import OperationsSession
        from vcfops_rotate.mock import MockOperationsServer
    except Exception:
        record(
            "package imports",
            False,
            "could not import vcfops_rotate.OperationsSession / "
            "vcfops_rotate.mock.MockOperationsServer:\n"
            + traceback.format_exc(limit=6),
        )
        return 1
    record("package imports", True)

    credentials = [
        {
            "id": CRED_ID,
            "name": "vc-collector-principal",
            "adapterKindKey": "VMWARE",
            "credentialKindKey": "PRINCIPALCREDENTIAL",
            "editable": True,
            "fields": [{"name": "USER", "value": USER}],
        },
        {
            "id": OTHER_CRED_ID,
            "name": "openapi-collector",
            "adapterKindKey": "OPENAPI",
            "credentialKindKey": "PRINCIPALCREDENTIAL",
            "editable": True,
            "fields": [{"name": "USER", "value": USER}],
        },
    ]

    log_path = ROOT / ".mock-logs" / "verify-requests.jsonl"
    if log_path.exists():
        log_path.unlink()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    mock = MockOperationsServer(
        contract_path=str(CONTRACT_PATH),
        users={USER: OLD_SECRET},
        credentials=credentials,
        log_path=str(log_path),
    )
    mock.start()
    failed = 0
    session = None
    try:
        if not check("mock binds a loopback origin", lambda: require_loopback_base(mock)):
            return 1

        session = OperationsSession(
            base_url=mock.base_url,
            username=USER,
            password=OLD_SECRET,
            contract_path=str(CONTRACT_PATH),
        )

        failed += not check("acquireToken wire shape", lambda: t_acquire(mock, session))
        old_token = session.active_token

        failed += not check("optional query parameters omitted when unset",
                            lambda: t_query_omitted(mock, session))
        failed += not check("array query parameters are exploded, not joined",
                            lambda: t_query_exploded(mock, session))
        failed += not check("loopback requests ignore ambient HTTP proxies",
                            lambda: t_proxy_independent(mock, session))
        failed += not check("path parameter interpolation",
                            lambda: t_path_param(mock, session))
        failed += not check("PATCH body omits unset optional fields",
                            lambda: t_patch_omits(mock, session))
        failed += not check("PATCH body keeps optional fields that are set to false",
                            lambda: t_patch_keeps_false(mock, session))
        failed += not check("authSource omitted when unset, sent when set",
                            lambda: t_auth_source(mock, OperationsSession, CONTRACT_PATH))
        failed += not check("bad credentials surface as a 401 error",
                            lambda: t_bad_password(mock, OperationsSession, CONTRACT_PATH))
        failed += not check("rotation does not strand in-flight requests",
                            lambda: t_rotation(mock, session, old_token))
        failed += not check("close releases once and is idempotent",
                            lambda: t_close_idempotent(
                                mock, OperationsSession, CONTRACT_PATH
                            ))
        failed += not check("mock enforces the contracted request surface",
                            lambda: t_mock_validation(mock, session))
        failed += not check("mock serves only the contracted operations",
                            lambda: t_out_of_contract(mock, session))
        failed += not check("request log is a readable append-only JSONL file",
                            lambda: t_log_file(mock, log_path))
    finally:
        try:
            if session is not None:
                session.close()
        except Exception:
            pass
        mock.stop()
    return failed


def require_loopback_base(mock) -> None:
    base = str(mock.base_url)
    require(
        base.startswith("http://127.0.0.1:"),
        f"mock.base_url must be an http origin on 127.0.0.1, got {base!r}",
    )
    require(
        not base.rstrip("/").endswith(BASE_PATH),
        f"mock.base_url must be the origin only; the client appends the contract "
        f"base_path {BASE_PATH!r}. Got {base!r}",
    )


def t_acquire(mock, session) -> None:
    session.open()
    require(session.active_token, "session.active_token is empty after open()")
    e = last_entry(mock, "acquireToken")
    eq(e["method"], "POST", "acquireToken method")
    eq(e["path"], f"{BASE_PATH}/api/auth/token/acquire", "acquireToken path")
    eq(e["query_string"], "", "acquireToken must not send a query string")
    eq(e["status"], 200, "acquireToken status")
    body = e["body_json"]
    require(isinstance(body, dict), "acquireToken body must be a JSON object")
    eq(sorted(body), ["password", "username"], "acquireToken body keys")
    eq(body["username"], USER, "acquireToken body.username")
    eq(body["password"], OLD_SECRET, "acquireToken body.password")
    eq(header(e, "content-type"), "application/json", "acquireToken Content-Type")
    eq(header(e, "accept"), "application/json", "acquireToken Accept")
    require(
        header(e, "authorization") is None,
        "acquireToken must not send an Authorization header",
    )


def t_query_omitted(mock, session) -> None:
    session.get_credentials()
    e = last_entry(mock, "getCredentials")
    eq(e["method"], "GET", "getCredentials method")
    eq(e["path"], f"{BASE_PATH}/api/credentials", "getCredentials path")
    eq(e["query_string"], "", "unset filters must produce no query string at all")
    eq(e["query_pairs"], [], "unset filters must produce no query parameters")
    eq(e["body_raw"], None, "GET must not carry a request body")
    eq(header(e, "accept"), "application/json", "getCredentials Accept")
    eq(
        header(e, "authorization"),
        AUTH_TEMPLATE.format(token=session.active_token),
        "getCredentials Authorization header",
    )

    session.get_credentials(credential_ids=[], adapter_kinds=[])
    e = last_entry(mock, "getCredentials")
    eq(e["query_string"], "", "empty filter lists must be treated as unset and omitted")


def t_query_exploded(mock, session) -> None:
    session.get_credentials(adapter_kinds=["VMWARE", "OPENAPI"])
    e = last_entry(mock, "getCredentials")
    eq(
        [list(p) for p in e["query_pairs"]],
        [["adapterKind", "VMWARE"], ["adapterKind", "OPENAPI"]],
        "adapterKind must be exploded into one parameter per value",
    )
    require(
        "id=" not in e["query_string"],
        f"unset 'id' filter leaked into the query string: {e['query_string']!r}",
    )

    session.get_credentials(credential_ids=[CRED_ID], adapter_kinds=["VMWARE"])
    e = last_entry(mock, "getCredentials")
    pairs = [tuple(p) for p in e["query_pairs"]]
    eq(sorted(pairs), sorted([("id", CRED_ID), ("adapterKind", "VMWARE")]),
       "combined filters query parameters")


def t_proxy_independent(mock, session) -> None:
    saved_getproxies = urllib.request.getproxies
    saved_proxy_bypass = urllib.request.proxy_bypass
    saved_opener = urllib.request._opener
    try:
        urllib.request.getproxies = lambda: {"http": "http://192.0.2.1:9"}
        urllib.request.proxy_bypass = lambda host: False
        urllib.request._opener = None
        session.get_credentials()
    finally:
        urllib.request.getproxies = saved_getproxies
        urllib.request.proxy_bypass = saved_proxy_bypass
        urllib.request._opener = saved_opener

    e = last_entry(mock, "getCredentials")
    eq(e["status"], 200, "loopback request status with a hostile ambient proxy")


def t_path_param(mock, session) -> None:
    session.get_adapter_instances_using_credential(CRED_ID)
    e = last_entry(mock, "getAdapterInstancesUsingCredential")
    eq(e["method"], "GET", "getAdapterInstancesUsingCredential method")
    eq(
        e["path"],
        f"{BASE_PATH}/api/credentials/{CRED_ID}/adapters",
        "getAdapterInstancesUsingCredential path",
    )
    eq(e["query_string"], "", "path-parameter operation must not send a query string")
    eq(e["status"], 200, "getAdapterInstancesUsingCredential status")


def t_patch_omits(mock, session) -> None:
    session.partial_update_credential(
        credential_id=CRED_ID,
        name="vc-collector-principal",
        adapter_kind_key="VMWARE",
        credential_kind_key="PRINCIPALCREDENTIAL",
        fields=[{"name": "PASSWORD", "value": NEW_SECRET}],
    )
    e = last_entry(mock, "partialUpdateCredential")
    eq(e["method"], "PATCH", "partialUpdateCredential method")
    eq(e["path"], f"{BASE_PATH}/api/credentials", "partialUpdateCredential path")
    eq(e["status"], 200, "partialUpdateCredential status")
    body = e["body_json"]
    require(isinstance(body, dict), "partialUpdateCredential body must be a JSON object")
    eq(
        sorted(body),
        ["adapterKindKey", "credentialKindKey", "fields", "id", "name"],
        "partialUpdateCredential body keys",
    )
    require(
        "editable" not in body,
        "unset optional field 'editable' must be omitted, not sent",
    )
    eq(body["id"], CRED_ID, "partialUpdateCredential body.id")
    eq(
        body["fields"],
        [{"name": "PASSWORD", "value": NEW_SECRET}],
        "partialUpdateCredential body.fields",
    )
    for field in body["fields"]:
        eq(sorted(field), ["name", "value"], "name-value entry keys")
    eq(header(e, "content-type"), "application/json", "partialUpdateCredential Content-Type")
    eq(header(e, "accept"), "application/json", "partialUpdateCredential Accept")


def t_patch_keeps_false(mock, session) -> None:
    session.partial_update_credential(
        credential_id=CRED_ID,
        name="vc-collector-principal",
        adapter_kind_key="VMWARE",
        credential_kind_key="PRINCIPALCREDENTIAL",
        editable=False,
    )
    body = last_entry(mock, "partialUpdateCredential")["body_json"]
    eq(
        sorted(body),
        ["adapterKindKey", "credentialKindKey", "editable", "id", "name"],
        "partialUpdateCredential body keys with editable=False",
    )
    eq(body["editable"], False,
       "editable=False is set, not unset, and must be sent as JSON false")
    require(
        "fields" not in body,
        "unset optional field 'fields' must be omitted, not sent as [] or null",
    )


def t_auth_source(mock, OperationsSession, contract_path) -> None:
    s2 = OperationsSession(
        base_url=mock.base_url,
        username=USER,
        password=OLD_SECRET,
        auth_source="Local Users",
        contract_path=str(contract_path),
    )
    s2.open()
    try:
        e = last_entry(mock, "acquireToken")
        body = e["body_json"]
        eq(sorted(body), ["authSource", "password", "username"],
           "acquireToken body keys when auth_source is set")
        eq(body["authSource"], "Local Users", "acquireToken body.authSource")
    finally:
        token = s2.active_token
        s2.close()
    released = [e for e in entries_for(mock, "releaseToken") if e.get("token") == token]
    eq(len(released), 1, "close() must release the session token exactly once")
    eq(released[0]["method"], "POST", "releaseToken method")
    eq(released[0]["path"], f"{BASE_PATH}/api/auth/token/release", "releaseToken path")
    eq(released[0]["body_raw"], None,
       "releaseToken takes no request body in the specification")
    eq(released[0]["status"], 200, "releaseToken status")


def t_bad_password(mock, OperationsSession, contract_path) -> None:
    bad = OperationsSession(
        base_url=mock.base_url,
        username=USER,
        password="not-the-password",
        contract_path=str(contract_path),
    )
    try:
        bad.open()
    except Exception as exc:
        status = getattr(exc, "status", None)
        eq(status, 401, "error raised for a rejected acquireToken must carry .status")
        return
    raise Failure("open() with a wrong password must raise, but it succeeded")


def t_rotation(mock, session, old_token) -> None:
    eq(session.active_token, old_token, "active token before rotation")

    mock.set_password(USER, NEW_SECRET)
    mock.hold("getCredentials")
    mock.hold("getAdapterInstancesUsingCredential")

    inflight_results = {}

    def inflight(label, fn):
        try:
            inflight_results[label] = ("value", fn())
        except BaseException as exc:  # noqa: BLE001 - reported below
            inflight_results[label] = ("error", exc)

    workers = [
        threading.Thread(
            target=inflight,
            args=("credentials", lambda: session.get_credentials(adapter_kinds=["VMWARE"])),
            name="inflight-credentials",
            daemon=True,
        ),
        threading.Thread(
            target=inflight,
            args=(
                "adapters",
                lambda: session.get_adapter_instances_using_credential(CRED_ID),
            ),
            name="inflight-adapters",
            daemon=True,
        ),
    ]
    workers[0].start()
    require(
        mock.wait_started("getCredentials", timeout=WAIT),
        "the held getCredentials request never reached the mock",
    )
    workers[1].start()
    require(
        mock.wait_started("getAdapterInstancesUsingCredential", timeout=WAIT),
        "the held getAdapterInstancesUsingCredential request never reached the mock",
    )

    rot = {}

    def do_rotate():
        try:
            rot["value"] = session.rotate_secret(NEW_SECRET)
        except BaseException as exc:  # noqa: BLE001 - reported below
            rot["error"] = exc

    rotator = threading.Thread(target=do_rotate, name="rotate", daemon=True)
    rotator.start()
    rotator.join(WAIT)
    require(
        not rotator.is_alive(),
        "rotate_secret() blocked while a request was in flight; it must install the "
        "new token and return, releasing the old token asynchronously once the last "
        "request using it finishes",
    )
    if "error" in rot:
        raise Failure(f"rotate_secret() raised {rot['error']!r}")
    result = rot["value"]

    eq(getattr(result, "previous_token", None), old_token, "RotationResult.previous_token")
    new_token = getattr(result, "active_token", None)
    require(new_token and new_token != old_token,
            "RotationResult.active_token must be a new, different token")
    eq(session.active_token, new_token, "session.active_token after rotation")
    eq(getattr(result, "inflight_at_rotation", None), 2,
       "RotationResult.inflight_at_rotation")
    eq(getattr(result, "released", None), False,
       "RotationResult.released must be False while the old token is still in use")

    acquires = entries_for(mock, "acquireToken")
    eq(acquires[-1]["body_json"]["password"], NEW_SECRET,
       "rotation must acquire the new token using the new password")
    require(
        header(acquires[-1], "authorization") is None,
        "the token-acquiring call during rotation must be unauthenticated",
    )

    stale = [e for e in entries_for(mock, "releaseToken") if e.get("token") == old_token]
    require(
        not stale,
        "the old token was released while a request was still in flight on it — "
        "that is exactly the stranding this tool exists to prevent",
    )

    mock.release("getCredentials")
    workers[0].join(WAIT)
    require(not workers[0].is_alive(), "the in-flight getCredentials never completed")
    kind, value = inflight_results.get("credentials", ("error", "no result"))
    if kind == "error":
        raise Failure(
            "the in-flight request failed across the rotation: "
            f"{value!r}"
        )

    first_held = [
        e for e in entries_for(mock, "getCredentials") if e.get("token") == old_token
    ][-1]
    eq(first_held["status"], 200, "the first in-flight request must complete successfully")
    eq(
        header(first_held, "authorization"),
        AUTH_TEMPLATE.format(token=old_token),
        "the in-flight request must finish on the token it started with",
    )
    stale = [e for e in entries_for(mock, "releaseToken") if e.get("token") == old_token]
    require(
        not stale,
        "the old token was released after the first of two in-flight requests; "
        "release must wait for the last request",
    )

    mock.release("getAdapterInstancesUsingCredential")
    workers[1].join(WAIT)
    require(not workers[1].is_alive(), "the second in-flight request never completed")
    kind, value = inflight_results.get("adapters", ("error", "no result"))
    if kind == "error":
        raise Failure(
            "the second in-flight request failed across the rotation: "
            f"{value!r}"
        )

    second_held = [
        e for e in entries_for(mock, "getAdapterInstancesUsingCredential")
        if e.get("token") == old_token
    ][-1]
    eq(second_held["status"], 200, "the second in-flight request must complete successfully")
    eq(
        header(second_held, "authorization"),
        AUTH_TEMPLATE.format(token=old_token),
        "each in-flight request must finish on the token it started with",
    )

    wait_for(
        lambda: [e for e in entries_for(mock, "releaseToken") if e.get("token") == old_token],
        what="the old token to be released after its last request drained",
    )
    released = [e for e in entries_for(mock, "releaseToken") if e.get("token") == old_token]
    eq(len(released), 1, "the old token must be released exactly once")
    require(
        released[0]["completed_ns"] >= max(
            first_held["completed_ns"], second_held["completed_ns"]
        ),
        "releaseToken for the old token was logged before the in-flight request "
        "it was supposed to wait for",
    )

    session.get_credentials()
    after = last_entry(mock, "getCredentials")
    eq(after["token"], new_token, "requests started after rotation must use the new token")
    eq(after["status"], 200, "post-rotation request status")

    idle_result = session.rotate_secret(NEW_SECRET)
    idle_token = getattr(idle_result, "active_token", None)
    require(idle_token and idle_token != new_token,
            "an idle rotation must install another fresh token")
    eq(getattr(idle_result, "previous_token", None), new_token,
       "idle RotationResult.previous_token")
    eq(getattr(idle_result, "inflight_at_rotation", None), 0,
       "idle RotationResult.inflight_at_rotation")
    eq(getattr(idle_result, "released", None), True,
       "idle RotationResult.released must be true after synchronous release")
    idle_releases = [
        e for e in entries_for(mock, "releaseToken") if e.get("token") == new_token
    ]
    eq(len(idle_releases), 1,
       "an idle rotation must release its previous token during rotate_secret")
    eq(session.active_token, idle_token, "active token after idle rotation")


def t_close_idempotent(mock, OperationsSession, contract_path) -> None:
    session = OperationsSession(
        base_url=mock.base_url,
        username=USER,
        password=NEW_SECRET,
        contract_path=str(contract_path),
    )
    session.open()
    token = session.active_token
    session.close()
    session.close()

    eq(session.active_token, None, "active_token after close")
    released = [e for e in entries_for(mock, "releaseToken") if e.get("token") == token]
    eq(len(released), 1, "repeated close() calls must release the active token once")
    eq(released[0]["body_raw"], None, "close releaseToken must not send a body")
    eq(header(released[0], "accept"), "application/json", "releaseToken Accept")


def t_mock_validation(mock, session) -> None:
    token = session.active_token
    credentials_url = f"{mock.base_url}{BASE_PATH}/api/credentials"
    acquire_url = f"{mock.base_url}{BASE_PATH}/api/auth/token/acquire"
    release_url = f"{mock.base_url}{BASE_PATH}/api/auth/token/release"

    eq(raw_request(f"{credentials_url}?unexpected=value", token=token), 400,
       "mock response to an undeclared query parameter")
    eq(raw_request(credentials_url, method="GET", token=token,
                   body={"unexpected": True}), 400,
       "mock response to a body on an operation with request_body null")
    eq(raw_request(credentials_url, method="POST", token=token), 404,
       "mock response to the wrong method for a contracted path")

    eq(raw_request(acquire_url, method="POST", body={"username": USER}), 400,
       "mock response to a missing required body field")
    eq(raw_request(
        acquire_url,
        method="POST",
        body={"username": USER, "password": NEW_SECRET, "unexpected": True},
    ), 400, "mock response to an undeclared body field")
    eq(raw_request(
        acquire_url,
        method="POST",
        body={"username": USER, "password": NEW_SECRET, "authSource": ""},
    ), 400, "mock response to an empty optional body field")

    required_patch = {
        "name": "vc-collector-principal",
        "adapterKindKey": "VMWARE",
        "credentialKindKey": "PRINCIPALCREDENTIAL",
    }
    eq(raw_request(
        credentials_url,
        method="PATCH",
        token=token,
        body={**required_patch, "editable": None},
    ), 400, "mock response to a null optional body field")
    eq(raw_request(release_url, method="POST", token=token, body={}), 400,
       "mock response to a body on releaseToken")

    session.get_credentials()
    eq(last_entry(mock, "getCredentials")["status"], 200,
       "a rejected releaseToken body must not deactivate the token")


def t_out_of_contract(mock, session) -> None:
    token = session.active_token
    status = raw_request(f"{mock.base_url}{BASE_PATH}/api/credentialkinds", token=token)
    eq(status, 404,
       "the mock must serve only the operations named in the contract; "
       "getCredentialKinds is not one of them")
    e = mock.entries()[-1]
    require(
        e.get("operation_id") is None,
        f"an out-of-contract request must be logged with operation_id null, got "
        f"{e.get('operation_id')!r}",
    )
    eq(e["status"], 404, "out-of-contract request status")

    status = raw_request(f"{mock.base_url}{BASE_PATH}/api/credentials", token=None)
    eq(status, 401, "a request with no Authorization header must be rejected with 401")

    status = raw_request(f"{mock.base_url}{BASE_PATH}/api/credentials", token="not-a-token")
    eq(status, 401, "a request with an unknown token must be rejected with 401")


def t_log_file(mock, log_path) -> None:
    require(log_path.exists(), f"the mock did not write a request log at {log_path}")
    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    require(lines, "the request log is empty")
    parsed = [json.loads(ln) for ln in lines]
    seqs = [e["seq"] for e in parsed]
    eq(seqs, sorted(seqs), "request log 'seq' must increase monotonically")
    eq(len(set(seqs)), len(seqs), "request log 'seq' values must be unique")
    eq(
        [e["seq"] for e in mock.entries()],
        seqs,
        "mock.entries() must return the same records, in the same order, as the log file",
    )
    for e in parsed:
        for key in ("seq", "operation_id", "method", "path", "query_string",
                    "query_pairs", "headers", "body_raw", "body_json", "token",
                    "status", "started_ns", "completed_ns"):
            require(key in e, f"request log entry {e.get('seq')} is missing key {key!r}")
        require(
            e["completed_ns"] >= e["started_ns"],
            f"request log entry {e['seq']} has completed_ns before started_ns",
        )


# --------------------------------------------------------------------------

def main() -> int:
    print(f"vcfops_rotate acceptance checks  (root: {ROOT})\n", flush=True)
    install_loopback_guard()

    failed = 0
    failed += not check("docs/official_sources.json records the specification revision",
                        check_sources)
    contract_ok = check("docs/contract.json matches the specification", check_contract)
    failed += not contract_ok
    failed += not check("sources import only the standard library", check_stdlib_only)

    if not contract_ok:
        record("runtime behaviour", False,
               "skipped: the contract must be correct before the mock can be pinned to it")
        failed += 1
    else:
        failed += run_dynamic()

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{total} checks passed", flush=True)
    if failed:
        print("RESULT: FAIL", flush=True)
        return 1
    print("RESULT: PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
