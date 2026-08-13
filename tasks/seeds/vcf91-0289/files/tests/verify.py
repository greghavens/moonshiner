"""Deterministic verifier for the VCF Operations for Networks certificate rotation.

Nothing here talks to a live appliance: every case runs against the loopback mock
in ``tests/mock_vcfonw_server.py``, which is itself pinned to ``docs/contract.json``.
The mock's JSON Lines request log is the evidence; the checks below assert the
exact wire shape that was put on it, including that optional fields the caller did
not supply are absent from the request body rather than sent as ``null`` or ``""``.

    python3 tests/verify.py
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import mock_vcfonw_server as mock  # noqa: E402

PACKAGE_DIR = os.path.join(REPO_ROOT, "vcfonw_certrotate")
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")
SOURCES_PATH = os.path.join(REPO_ROOT, "docs", "official_sources.json")
RUN_CASE = os.path.join(REPO_ROOT, "tests", "run_case.py")

# --- ground truth, read out of the pinned specification revision -------------
SPEC_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
BASE_PATH = "/api/ni"
AUTH_PREFIX = "NetworkInsight"
EXPECTED_OPERATIONS = {
    "authenticate": ("create", "POST", "/auth/token", 200),
    "submit_certificate_update": (
        "updateCertificate", "PUT", "/settings/certificates/{id}", 202),
    "poll_update_status": (
        "fetchCertificateUpdateStatusForUpdateId", "GET",
        "/settings/certificates/status/{id}", 200),
    "revoke_token": ("delete", "DELETE", "/auth/token", 204),
}
TERMINAL = ["SUCCESS", "FAILED"]
NON_TERMINAL = ["SUBMITTED", "IN_PROGRESS"]

# --- fixtures ---------------------------------------------------------------
USERNAME = "admin@local"
PASSWORD = "VMware1!VMware1!"
CERT_ID = "platform-web-cert"
CERT_PEM = "-----BEGIN CERTIFICATE-----\nRkFLRS1DRVJUSUZJQ0FURS1GT1ItVEVTVA==\n-----END CERTIFICATE-----\n"
KEY_PEM = "-----BEGIN PRIVATE KEY-----\nRkFLRS1QUklWQVRFLUtFWS1GT1ItVEVTVA==\n-----END PRIVATE KEY-----\n"
CHAIN_PEM = "-----BEGIN CERTIFICATE-----\nRkFLRS1ST09ULUNBLUNIQUlO\n-----END CERTIFICATE-----\n"


class Failures:
    def __init__(self):
        self.items = []

    def check(self, condition, message):
        if not condition:
            self.items.append(message)
        return bool(condition)

    def equal(self, actual, expected, message):
        return self.check(
            actual == expected,
            "%s (expected %r, got %r)" % (message, expected, actual),
        )


F = Failures()


# --- static checks -----------------------------------------------------------
def check_pinned_docs():
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        contract = json.load(handle)
    source = contract.get("source", {})
    F.equal(source.get("commit_sha"), SPEC_COMMIT, "contract source.commit_sha")
    F.equal(source.get("spec_path"), SPEC_PATH, "contract source.spec_path")
    F.equal(source.get("server_base_path"), BASE_PATH, "contract source.server_base_path")
    F.equal(contract.get("security", {}).get("value_prefix"), AUTH_PREFIX,
            "contract security.value_prefix")

    status_model = contract.get("status_model", {})
    F.equal(sorted(status_model.get("terminal", [])), sorted(TERMINAL),
            "contract status_model.terminal")
    F.equal(sorted(status_model.get("non_terminal", [])), sorted(NON_TERMINAL),
            "contract status_model.non_terminal")

    by_role = {op.get("role"): op for op in contract.get("operations", [])}
    F.equal(sorted(by_role), sorted(EXPECTED_OPERATIONS), "contract operation roles")
    for role, (op_id, method, path, status) in EXPECTED_OPERATIONS.items():
        op = by_role.get(role)
        if not F.check(op is not None, "contract is missing role %s" % role):
            continue
        F.equal(op.get("operationId"), op_id, "contract %s operationId" % role)
        F.equal(op.get("method"), method, "contract %s method" % role)
        F.equal(op.get("path"), path, "contract %s path" % role)
        F.equal(op.get("success", {}).get("status"), status,
                "contract %s success status" % role)

    with open(SOURCES_PATH, "r", encoding="utf-8") as handle:
        sources = json.load(handle)
    F.equal(sources.get("repository_commit_sha"), SPEC_COMMIT,
            "official_sources repository_commit_sha")
    F.equal(sources.get("spec_path"), SPEC_PATH, "official_sources spec_path")
    F.equal(sources.get("repository"), "https://github.com/vmware/vcf-api-specs",
            "official_sources repository")
    logged = {entry.get("operationId") for entry in sources.get("operations", [])}
    F.equal(sorted(logged), sorted(op[0] for op in EXPECTED_OPERATIONS.values()),
            "official_sources operationIds")
    for entry in sources.get("operations", []):
        F.equal(entry.get("spec_path"), SPEC_PATH,
                "official_sources spec_path for %s" % entry.get("operationId"))
        F.equal(entry.get("repository_commit_sha"), SPEC_COMMIT,
                "official_sources commit sha for %s" % entry.get("operationId"))


def check_stdlib_only():
    stdlib = getattr(sys, "stdlib_module_names", None)
    if stdlib is None:  # pragma: no cover - Python < 3.10
        return
    allowed = set(stdlib) | {"vcfonw_certrotate", "__future__"}
    for name in sorted(os.listdir(PACKAGE_DIR)):
        if not name.endswith(".py"):
            continue
        full = os.path.join(PACKAGE_DIR, name)
        with open(full, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=full)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    F.check(root in allowed,
                            "vcfonw_certrotate/%s imports non-stdlib module %r" % (name, root))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                root = (node.module or "").split(".")[0]
                F.check(root in allowed,
                        "vcfonw_certrotate/%s imports non-stdlib module %r" % (name, root))


# --- case runner -------------------------------------------------------------
def run_case(case_id, scenario, rotate_kwargs, workdir, fault=None,
             deterministic_clock=False):
    log_path = os.path.join(workdir, "%s.log.jsonl" % case_id)
    server, base_url, state = mock.start(
        log_path=log_path,
        case_id=case_id,
        scenario=scenario,
        contract_path=CONTRACT_PATH,
        username=USERNAME,
        password=PASSWORD,
        certificate_id=CERT_ID,
        fault=fault,
    )
    case_path = os.path.join(workdir, "%s.case.json" % case_id)
    with open(case_path, "w", encoding="utf-8") as handle:
        json.dump({
            "base_url": base_url,
            "timeout": 5.0,
            "rotate_kwargs": rotate_kwargs,
            "deterministic_clock": deterministic_clock,
        }, handle)
    try:
        completed = subprocess.run(
            [sys.executable, "-B", RUN_CASE, case_path],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=90,
        )
    finally:
        mock.stop(server)

    if completed.returncode != 0:
        F.check(False, "[%s] run_case exited %d: %s" % (
            case_id, completed.returncode, completed.stderr.strip()[:800]))
        return None, [], state

    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        F.check(False, "[%s] run_case produced no JSON result: %r" % (
            case_id, completed.stdout[:800]))
        return None, [], state

    entries = []
    with open(log_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    entries.sort(key=lambda e: e["seq"])
    return result, entries, state


def roles(entries):
    return [entry["role"] for entry in entries]


def header(entry, name):
    return entry["headers"].get(name)


def check_common(case_id, entries):
    for entry in entries:
        F.check(entry["role"] is not None,
                "[%s] request %d went to %s %s, which the contract does not name" % (
                    case_id, entry["seq"], entry["method"], entry["path"]))
        F.equal(entry["query"], "", "[%s] request %d carried a query string" % (
            case_id, entry["seq"]))
        host = header(entry, "host") or ""
        F.check(host.startswith("127.0.0.1"),
                "[%s] request %d Host was %r, expected loopback" % (
                    case_id, entry["seq"], host))


def check_auth_request(case_id, entry, expected_body):
    F.equal(entry["method"], "POST", "[%s] token request method" % case_id)
    F.equal(entry["path"], BASE_PATH + "/auth/token", "[%s] token request path" % case_id)
    F.check(header(entry, "authorization") is None,
            "[%s] token request carried an Authorization header; the spec gives "
            "operation 'create' an empty security requirement" % case_id)
    content_type = header(entry, "content-type") or ""
    F.check(content_type.startswith("application/json"),
            "[%s] token request Content-Type was %r" % (case_id, content_type))
    body = entry["body_json"]
    if not F.check(isinstance(body, dict), "[%s] token request body was not JSON" % case_id):
        return
    F.equal(sorted(body), sorted(expected_body),
            "[%s] token request body keys" % case_id)
    F.equal(body, expected_body, "[%s] token request body" % case_id)


def check_submit_request(case_id, entry, token, expected_body):
    F.equal(entry["method"], "PUT", "[%s] submit request method" % case_id)
    F.equal(entry["path"], BASE_PATH + "/settings/certificates/" + CERT_ID,
            "[%s] submit request path" % case_id)
    F.equal(header(entry, "authorization"), "%s %s" % (AUTH_PREFIX, token),
            "[%s] submit request Authorization" % case_id)
    content_type = header(entry, "content-type") or ""
    F.check(content_type.startswith("application/json"),
            "[%s] submit request Content-Type was %r" % (case_id, content_type))
    body = entry["body_json"]
    if not F.check(isinstance(body, dict), "[%s] submit request body was not JSON" % case_id):
        return
    F.equal(sorted(body), sorted(expected_body), "[%s] submit request body keys" % case_id)
    F.equal(body, expected_body, "[%s] submit request body" % case_id)


def check_poll_request(case_id, entry, token, update_id):
    F.equal(entry["method"], "GET", "[%s] poll request method" % case_id)
    F.equal(entry["path"], BASE_PATH + "/settings/certificates/status/" + update_id,
            "[%s] poll request path must address the update id from the 202 response"
            % case_id)
    F.equal(header(entry, "authorization"), "%s %s" % (AUTH_PREFIX, token),
            "[%s] poll request Authorization" % case_id)
    F.check(not entry["body_text"], "[%s] poll request carried a body" % case_id)


def check_revoke_request(case_id, entry, token):
    F.equal(entry["method"], "DELETE", "[%s] token delete method" % case_id)
    F.equal(entry["path"], BASE_PATH + "/auth/token", "[%s] token delete path" % case_id)
    F.equal(header(entry, "authorization"), "%s %s" % (AUTH_PREFIX, token),
            "[%s] token delete Authorization" % case_id)
    F.check(not entry["body_text"], "[%s] token delete carried a body" % case_id)


def base_kwargs(**overrides):
    kwargs = {
        "username": USERNAME,
        "password": PASSWORD,
        "certificate_id": CERT_ID,
        "certificate_pem": CERT_PEM,
        "private_key_pem": KEY_PEM,
        "poll_interval": 0.01,
        "poll_timeout": 20.0,
    }
    kwargs.update(overrides)
    return kwargs


def check_success_case(case_id, scenario, rotate_kwargs, expected_token_body,
                       expected_submit_body, workdir):
    result, entries, state = run_case(case_id, scenario, rotate_kwargs, workdir)
    if result is None:
        return
    token = state.token
    update_id = "cert-update-%s-0001" % case_id
    polls = len(mock.SCENARIOS[scenario])
    terminal = mock.SCENARIOS[scenario][-1]

    check_common(case_id, entries)
    expected_roles = (["authenticate", "submit_certificate_update"]
                      + ["poll_update_status"] * polls + ["revoke_token"])
    if not F.equal(roles(entries), expected_roles, "[%s] request sequence" % case_id):
        return

    F.equal([e["status"] for e in entries],
            [200, 202] + [200] * polls + [204],
            "[%s] response statuses" % case_id)

    check_auth_request(case_id, entries[0], expected_token_body)
    check_submit_request(case_id, entries[1], token, expected_submit_body)
    for entry in entries[2:2 + polls]:
        check_poll_request(case_id, entry, token, update_id)
    check_revoke_request(case_id, entries[-1], token)

    if not F.check(result.get("ok"), "[%s] rotate() failed: %s" % (
            case_id, json.dumps(result)[:600])):
        return
    F.check(result.get("is_rotation_outcome"),
            "[%s] rotate() must return a RotationOutcome" % case_id)
    outcome = result["outcome"]
    F.equal(outcome.get("update_id"), update_id, "[%s] outcome.update_id" % case_id)
    F.equal(outcome.get("status"), terminal, "[%s] outcome.status" % case_id)
    F.equal(outcome.get("poll_count"), polls, "[%s] outcome.poll_count" % case_id)
    F.equal(outcome.get("succeeded"), terminal == "SUCCESS",
            "[%s] outcome.succeeded" % case_id)
    if terminal == "SUCCESS":
        F.equal(outcome.get("error_message"), None, "[%s] outcome.error_message" % case_id)
        F.equal(outcome.get("updated_nodes"), [mock.PLATFORM_NODE, mock.PROXY_NODE],
                "[%s] outcome.updated_nodes" % case_id)
        F.equal(outcome.get("failed_nodes"), [],
                "[%s] outcome.failed_nodes" % case_id)
    else:
        F.equal(outcome.get("error_message"), mock.FAILURE_MESSAGE,
                "[%s] outcome.error_message" % case_id)
        F.equal(outcome.get("updated_nodes"), [mock.PLATFORM_NODE],
                "[%s] outcome.updated_nodes" % case_id)
        F.equal(outcome.get("failed_nodes"), [mock.PROXY_NODE],
                "[%s] outcome.failed_nodes" % case_id)


def check_timeout_case(workdir):
    case_id = "stuck"
    result, entries, state = run_case(
        case_id, "stuck",
        base_kwargs(poll_interval=0.05, poll_timeout=0.4),
        workdir, deterministic_clock=True)
    if result is None:
        return
    token = state.token
    update_id = "cert-update-%s-0001" % case_id
    check_common(case_id, entries)

    observed = roles(entries)
    if not F.check(len(observed) >= 4, "[%s] expected auth, submit, polls and delete, got %r"
                   % (case_id, observed)):
        return
    F.equal(observed[0], "authenticate", "[%s] first request" % case_id)
    F.equal(observed[1], "submit_certificate_update", "[%s] second request" % case_id)
    F.equal(observed[-1], "revoke_token",
            "[%s] the auth token must still be deleted after a poll timeout" % case_id)
    poll_entries = entries[2:-1]
    F.check(all(role == "poll_update_status" for role in roles(poll_entries)),
            "[%s] unexpected request between submit and delete: %r" % (case_id, observed))
    F.check(2 <= len(poll_entries) <= 60,
            "[%s] expected repeated polling, saw %d polls" % (case_id, len(poll_entries)))
    for entry in poll_entries:
        check_poll_request(case_id, entry, token, update_id)
    check_revoke_request(case_id, entries[-1], token)

    F.equal(result.get("ok"), False, "[%s] rotate() must not return a value" % case_id)
    F.equal(result.get("error_type"), "PollTimeoutError", "[%s] raised error" % case_id)
    F.equal(result.get("update_id"), update_id, "[%s] PollTimeoutError.update_id" % case_id)
    F.equal(result.get("last_status"), "IN_PROGRESS",
            "[%s] PollTimeoutError.last_status" % case_id)
    F.equal(result.get("poll_count"), len(poll_entries),
            "[%s] PollTimeoutError.poll_count" % case_id)
    sleeps = result.get("clock", {}).get("sleeps")
    if F.check(isinstance(sleeps, list) and sleeps,
               "[%s] polling never slept between status reads" % case_id):
        F.check(all(abs(value - 0.05) < 1e-12 for value in sleeps),
                "[%s] expected every polling sleep to be poll_interval, got %r"
                % (case_id, sleeps))


def check_rejected_credentials_case(workdir):
    case_id = "badcreds"
    result, entries, _ = run_case(
        case_id, "success", base_kwargs(password="wrong-password"), workdir)
    if result is None:
        return
    check_common(case_id, entries)
    if F.equal(roles(entries), ["authenticate"],
               "[%s] a rejected token request must stop the flow" % case_id):
        F.equal(entries[0]["status"], 401, "[%s] token response status" % case_id)
    F.equal(result.get("ok"), False, "[%s] rotate() must not return a value" % case_id)
    F.equal(result.get("error_type"), "ApiError", "[%s] raised error" % case_id)
    F.equal(result.get("status"), 401, "[%s] ApiError.status" % case_id)
    F.equal(result.get("body"), {
        "code": 401,
        "message": "invalid credentials",
        "details": [],
    }, "[%s] ApiError.body" % case_id)


def check_post_auth_api_error_case(case_id, fault, expected_status,
                                   expected_message, expected_roles, workdir):
    result, entries, state = run_case(
        case_id, "success", base_kwargs(), workdir, fault=fault)
    if result is None:
        return
    token = state.token
    update_id = "cert-update-%s-0001" % case_id
    check_common(case_id, entries)
    if not F.equal(roles(entries), expected_roles,
                   "[%s] request sequence" % case_id):
        return

    check_auth_request(case_id, entries[0], {
        "username": USERNAME,
        "password": PASSWORD,
    })
    check_submit_request(case_id, entries[1], token, {
        "certificate": CERT_PEM,
        "private_key": KEY_PEM,
    })
    if fault == "poll":
        check_poll_request(case_id, entries[2], token, update_id)
    check_revoke_request(case_id, entries[-1], token)
    F.check(not state.token_valid,
            "[%s] token remained live after the API error" % case_id)

    F.equal(result.get("ok"), False,
            "[%s] rotate() must not return a value" % case_id)
    F.equal(result.get("error_type"), "ApiError", "[%s] raised error" % case_id)
    F.equal(result.get("status"), expected_status,
            "[%s] ApiError.status" % case_id)
    F.equal(result.get("body"), {
        "code": expected_status,
        "message": expected_message,
        "details": [],
    }, "[%s] ApiError.body" % case_id)


def check_redirect_case(workdir):
    case_id = "redirect"
    result, entries, _ = run_case(
        case_id, "success", base_kwargs(), workdir,
        fault="authenticate_redirect")
    if result is None:
        return
    check_common(case_id, entries)
    if F.equal(roles(entries), ["authenticate"],
               "[%s] a redirect must not issue an out-of-contract follow-up request"
               % case_id):
        check_auth_request(case_id, entries[0], {
            "username": USERNAME,
            "password": PASSWORD,
        })
        F.equal(entries[0]["status"], 302,
                "[%s] token response status" % case_id)
    F.equal(result.get("ok"), False,
            "[%s] rotate() must not return a value" % case_id)
    F.equal(result.get("error_type"), "ApiError", "[%s] raised error" % case_id)
    F.equal(result.get("status"), 302, "[%s] ApiError.status" % case_id)
    F.equal(result.get("body"), {
        "code": 302,
        "message": "authentication was redirected",
        "details": [],
    }, "[%s] ApiError.body" % case_id)


def check_revoke_error_case(workdir):
    case_id = "revokeerr"
    result, entries, state = run_case(
        case_id, "success", base_kwargs(), workdir, fault="revoke")
    if result is None:
        return
    token = state.token
    update_id = "cert-update-%s-0001" % case_id
    check_common(case_id, entries)
    expected_roles = (["authenticate", "submit_certificate_update"]
                      + ["poll_update_status"] * 3 + ["revoke_token"])
    if not F.equal(roles(entries), expected_roles,
                   "[%s] request sequence" % case_id):
        return
    check_auth_request(case_id, entries[0], {
        "username": USERNAME,
        "password": PASSWORD,
    })
    check_submit_request(case_id, entries[1], token, {
        "certificate": CERT_PEM,
        "private_key": KEY_PEM,
    })
    for entry in entries[2:-1]:
        check_poll_request(case_id, entry, token, update_id)
    check_revoke_request(case_id, entries[-1], token)

    F.equal(result.get("ok"), False,
            "[%s] rotate() must not return after token deletion failed" % case_id)
    F.equal(result.get("error_type"), "ApiError", "[%s] raised error" % case_id)
    F.equal(result.get("status"), 500, "[%s] ApiError.status" % case_id)
    F.equal(result.get("body"), {
        "code": 500,
        "message": "token deletion is unavailable",
        "details": [],
    }, "[%s] ApiError.body" % case_id)


def main():
    check_pinned_docs()
    check_stdlib_only()

    with tempfile.TemporaryDirectory(prefix="vcfonw-verify-") as workdir:
        # Nothing optional supplied: neither `domain` nor `chain` may appear at all.
        check_success_case(
            "minimal", "success", base_kwargs(),
            {"username": USERNAME, "password": PASSWORD},
            {"certificate": CERT_PEM, "private_key": KEY_PEM},
            workdir)

        # Every optional supplied: the same fields must now be present.
        check_success_case(
            "ldap", "success",
            base_kwargs(domain_type="LDAP", domain_value="corp.example.com",
                        chain_pem=CHAIN_PEM),
            {"username": USERNAME, "password": PASSWORD,
             "domain": {"domain_type": "LDAP", "value": "corp.example.com"}},
            {"certificate": CERT_PEM, "private_key": KEY_PEM, "chain": CHAIN_PEM},
            workdir)

        # A LOCAL domain carries no value: the nested optional drops out too.
        check_success_case(
            "local", "success", base_kwargs(domain_type="LOCAL"),
            {"username": USERNAME, "password": PASSWORD,
             "domain": {"domain_type": "LOCAL"}},
            {"certificate": CERT_PEM, "private_key": KEY_PEM},
            workdir)

        # The fields are independently optional in the pinned Domain schema.
        check_success_case(
            "domainvalue", "success",
            base_kwargs(domain_value="corp.example.com"),
            {"username": USERNAME, "password": PASSWORD,
             "domain": {"value": "corp.example.com"}},
            {"certificate": CERT_PEM, "private_key": KEY_PEM},
            workdir)

        # A terminal FAILED is a result, not an exception.
        check_success_case(
            "failed", "failure", base_kwargs(),
            {"username": USERNAME, "password": PASSWORD},
            {"certificate": CERT_PEM, "private_key": KEY_PEM},
            workdir)

        check_timeout_case(workdir)
        check_rejected_credentials_case(workdir)
        check_redirect_case(workdir)
        check_revoke_error_case(workdir)
        check_post_auth_api_error_case(
            "submiterr", "submit", 409,
            "a certificate update is already in progress",
            ["authenticate", "submit_certificate_update", "revoke_token"],
            workdir)
        check_post_auth_api_error_case(
            "pollerr", "poll", 500,
            "certificate update status is unavailable",
            ["authenticate", "submit_certificate_update", "poll_update_status",
             "revoke_token"],
            workdir)

    if F.items:
        print("FAIL: %d check(s) failed\n" % len(F.items))
        for item in F.items:
            print("  - %s" % item)
        return 1
    print("PASS: certificate rotation matches the pinned contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
