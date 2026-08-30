#!/usr/bin/env python3
"""Protected verifier for the VCF Operations for Networks bulk data source client.

Compiles the client and the TestMain harness, runs the harness against the loopback
mock in tools/mock_vcfon.py, then asserts the exact wire shape of every request the
client made, as recorded in the mock's request log.

No live VMware endpoint is contacted. Everything runs on 127.0.0.1.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import mock_vcfon as mock  # noqa: E402

CLIENT_SRC = os.path.join(ROOT, "src", "main", "java", "com", "vmware", "vcf", "opsnet",
                          "VcfOpsNetworksClient.java")
TEST_SRC = os.path.join(ROOT, "src", "test", "java", "com", "vmware", "vcf", "opsnet",
                        "TestMain.java")

AUTH_PATH = "/api/ni/auth/token"
BULK_PATH = "/api/ni/data-sources/bulk"
VIEW_PATH = "/api/ni/data-sources/bulk/view-details/" + mock.REQUEST_ID

# The request body `bulkDataSourceOperation` must carry, given the TestMain fixture.
# `action_on_field` appears only on the fields where the fixture set it; the field
# with an explicitly empty `value` still carries that value.
EXPECTED_BULK_BODY = {
    "action_type": "UPDATE",
    "data_sources": [
        {
            "entity_id": mock.ENTITY_A,
            "fields": [
                {"key": "nickname", "value": "edge-esx-01"},
                {"key": "tags", "value": "prod,dc-a", "action_on_field": "ADD_TAGS"},
            ],
        },
        {
            "entity_id": mock.ENTITY_B,
            "fields": [
                {"key": "tags", "value": "legacy", "action_on_field": "REMOVE_TAGS"},
            ],
        },
        {
            "entity_id": mock.ENTITY_C,
            "fields": [
                {"key": "notes", "value": ""},
            ],
        },
        {
            "entity_id": mock.ENTITY_D,
            "fields": [
                {"key": "tags", "value": "prod,dc-b", "action_on_field": "OVERRIDE_TAGS"},
            ],
        },
    ],
}

EXPECTED_AUTH_BODY = {
    "username": "admin@vrni.local",
    "password": "S3cure-Pass!2026",
}

EXPECTED_SVC_AUTH_BODY = {
    "username": "svc-integration@corp.example.com",
    "password": "R0tate-Me-90d",
    "domain": {"domain_type": "LDAP", "value": ""},
}

EXPECTED_LOCAL_AUTH_BODY = {
    "username": "local-automation",
    "password": "L0cal-Pass!",
    "domain": {"domain_type": "LOCAL"},
}

EXPECTED_RESULT = {
    "total_count": 4,
    "success_count": 3,
    "failed_count": 1,
    "successful_data_sources": [mock.ENTITY_A, mock.ENTITY_B, mock.ENTITY_C],
    "failed_data_sources": [{"entity_id": mock.ENTITY_D, "reason": mock.FAIL_REASON}],
}

class Failure(Exception):
    pass


class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        self.results.append((name, bool(condition), detail))
        return bool(condition)

    def equal(self, name: str, actual, expected) -> bool:
        ok = actual == expected
        detail = "" if ok else f"expected {json.dumps(expected, sort_keys=True)}, got {json.dumps(actual, sort_keys=True, default=str)}"
        return self.check(name, ok, detail)

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [r for r in self.results if not r[1]]

    def report(self) -> int:
        width = max(len(n) for n, _, _ in self.results)
        for name, ok, detail in self.results:
            mark = "PASS" if ok else "FAIL"
            line = f"  [{mark}] {name.ljust(width)}"
            if detail and not ok:
                line += f"  -- {detail}"
            print(line)
        print()
        if self.failed:
            print(f"FAILED: {len(self.failed)} of {len(self.results)} checks failed")
            return 1
        print(f"PASSED: {len(self.results)} checks")
        return 0


def find_nulls(value, path="$"):
    """Return the JSON paths at which a null appears."""
    found = []
    if value is None:
        return [path]
    if isinstance(value, dict):
        for k, v in value.items():
            found.extend(find_nulls(v, f"{path}.{k}"))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found.extend(find_nulls(v, f"{path}[{i}]"))
    return found


def compile_sources(build_dir: str) -> tuple[bool, str]:
    javac = shutil.which("javac")
    if not javac:
        raise Failure("javac not found on PATH")
    proc = subprocess.run(
        [javac, "-nowarn", "-d", build_dir, CLIENT_SRC, TEST_SRC],
        capture_output=True, text=True, timeout=300,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def run_harness(build_dir: str, base_url: str, *args: str, timeout: int = 180):
    java = shutil.which("java")
    if not java:
        raise Failure("java not found on PATH")
    env = dict(os.environ)
    env["VCFON_BASE_URL"] = base_url
    return subprocess.run(
        [java, "-cp", build_dir, "com.vmware.vcf.opsnet.TestMain", *args],
        capture_output=True, text=True, timeout=timeout, env=env, cwd=ROOT,
    )


def stdout_value(stdout: str, prefix: str):
    for line in stdout.splitlines():
        if line.startswith(prefix + " "):
            return line[len(prefix) + 1:].strip()
    return None


def main() -> int:
    c = Checks()

    for label, path in (("client source", CLIENT_SRC), ("harness source", TEST_SRC)):
        if not os.path.isfile(path):
            print(f"missing {label}: {path}")
            return 1

    workdir = tempfile.mkdtemp(prefix="vcfon-verify-")
    build_dir = os.path.join(workdir, "build")
    os.makedirs(build_dir, exist_ok=True)
    log_path = os.path.join(workdir, "requests.jsonl")

    print("VCF Operations for Networks 9.1 - bulk data source client verification")
    print(f"  workdir: {workdir}")

    compiled, compile_output = compile_sources(build_dir)
    c.check("compiles", compiled, compile_output[:2000])
    if not compiled:
        print()
        return c.report()

    server, _thread, port = mock.start_server(log_path)
    base_url = f"http://127.0.0.1:{port}"
    print(f"  mock:    {base_url}")
    try:
        proc = run_harness(build_dir, base_url)
    finally:
        server.shutdown()
        server.server_close()

    print()
    if proc.stdout.strip():
        print("harness stdout:")
        for line in proc.stdout.strip().splitlines():
            print("  | " + line)
    if proc.stderr.strip():
        print("harness stderr:")
        for line in proc.stderr.strip().splitlines()[:40]:
            print("  | " + line)
    print()

    c.check("harness exits 0", proc.returncode == 0, f"exit code {proc.returncode}")

    entries = mock.read_log(log_path)
    c.check("client sent at least one request", len(entries) > 0)
    if not entries:
        return c.report()

    # ---- closed world ---------------------------------------------------
    unmatched = [e for e in entries if e["matched_operation_id"] is None]
    c.check(
        "calls only the operations the contract names",
        not unmatched,
        "unexpected: " + ", ".join(f"{e['method']} {e['path']}" for e in unmatched[:6]),
    )
    c.check(
        "no request carries a query string",
        all(not e["query"] for e in entries),
        "; ".join(f"{e['path']}?{e['query']}" for e in entries if e["query"])[:300],
    )
    c.check(
        "every request body parses as JSON",
        all(e["body_parse_error"] is None for e in entries),
        "; ".join(str(e["body_parse_error"]) for e in entries if e["body_parse_error"])[:300],
    )
    c.check(
        "no request was rejected by the platform",
        all(e["status"] < 400 for e in entries),
        "; ".join(f"{e['method']} {e['path']} -> {e['status']}" for e in entries if e["status"] >= 400)[:300],
    )

    auths = [e for e in entries if e["path"] == AUTH_PATH]
    bulks = [e for e in entries if e["path"] == BULK_PATH]
    polls = [e for e in entries if e["path"] == VIEW_PATH]

    c.check("calls operationId create three times", len(auths) == 3, f"got {len(auths)}")
    c.check("calls operationId bulkDataSourceOperation exactly once", len(bulks) == 1, f"got {len(bulks)}")

    # ---- operationId create (no domain) ---------------------------------
    if auths:
        a0 = auths[0]
        c.equal("create uses POST", a0["method"], "POST")
        c.check(
            "create sends Content-Type application/json",
            (a0["headers"].get("content-type") or "").split(";")[0].strip().lower() == "application/json",
            repr(a0["headers"].get("content-type")),
        )
        c.check(
            "create sends no Authorization header (spec declares security: [])",
            "authorization" not in a0["headers"],
            repr(a0["headers"].get("authorization")),
        )
        c.equal("create body matches the credential fixture", a0["body_json"], EXPECTED_AUTH_BODY)
        body = a0["body_json"] if isinstance(a0["body_json"], dict) else {}
        c.check(
            "unset optional `domain` is omitted, not sent empty",
            "domain" not in body,
            f"domain was sent as {json.dumps(body.get('domain'))}",
        )

    # ---- operationId create (domain set) --------------------------------
    if len(auths) >= 2:
        a1 = auths[1]
        c.equal("create serializes `domain` when it is set", a1["body_json"], EXPECTED_SVC_AUTH_BODY)
        domain = a1["body_json"].get("domain", {}) if isinstance(a1["body_json"], dict) else {}
        c.check(
            "set-but-empty optional domain `value` is still sent",
            isinstance(domain, dict) and "value" in domain and domain["value"] == "",
            json.dumps(domain, sort_keys=True),
        )

    # ---- operationId create (partial domain) ----------------------------
    if len(auths) >= 3:
        a2 = auths[2]
        c.equal(
            "create omits an unset member of a present `domain` object",
            a2["body_json"],
            EXPECTED_LOCAL_AUTH_BODY,
        )

    # ---- operationId bulkDataSourceOperation ----------------------------
    if bulks:
        b = bulks[0]
        c.equal("bulkDataSourceOperation uses POST", b["method"], "POST")
        c.check(
            "bulkDataSourceOperation sends Content-Type application/json",
            (b["headers"].get("content-type") or "").split(";")[0].strip().lower() == "application/json",
            repr(b["headers"].get("content-type")),
        )
        c.equal(
            "bulkDataSourceOperation sends the ApiKeyAuth header",
            b["headers"].get("authorization"),
            "NetworkInsight " + mock.TOKENS[0],
        )
        c.equal("bulkDataSourceOperation body is exactly the contract shape", b["body_json"], EXPECTED_BULK_BODY)

        # Targeted omission checks, so a failure says which rule was broken.
        body = b["body_json"] if isinstance(b["body_json"], dict) else {}
        sources = body.get("data_sources") or []
        fields = [f for s in sources if isinstance(s, dict) for f in (s.get("fields") or [])
                  if isinstance(f, dict)]
        unset_action = [f for f in fields if f.get("key") == "nickname"]
        c.check(
            "unset optional `action_on_field` is omitted, not sent empty",
            bool(unset_action) and all("action_on_field" not in f for f in unset_action),
            "; ".join(json.dumps(f, sort_keys=True) for f in unset_action)[:300],
        )
        set_action = [f for f in fields if f.get("key") == "tags"]
        c.check(
            "set `action_on_field` is serialized with its enum value",
            len(set_action) == 3 and all(f.get("action_on_field") in
                                         ("ADD_TAGS", "REMOVE_TAGS", "OVERRIDE_TAGS") for f in set_action),
            "; ".join(json.dumps(f, sort_keys=True) for f in set_action)[:300],
        )
        empty_value = [f for f in fields if f.get("key") == "notes"]
        c.check(
            "a set-but-empty `value` is still sent",
            len(empty_value) == 1 and empty_value[0].get("value") == "",
            "; ".join(json.dumps(f, sort_keys=True) for f in empty_value)[:300],
        )

    nulls = []
    for e in entries:
        if e["body_json"] is not None:
            nulls.extend(f"{e['path']} {p}" for p in find_nulls(e["body_json"]))
    c.check("no request body contains a JSON null", not nulls, "; ".join(nulls[:8]))

    # ---- operationId getBulkOperationDetails, polled to terminal --------
    c.check(
        f"polls getBulkOperationDetails to the terminal state (>= {mock.POLLS_TO_TERMINAL} polls)",
        len(polls) >= mock.POLLS_TO_TERMINAL,
        f"got {len(polls)} poll(s); the operation only reports "
        f"success_count + failed_count == total_count on poll {mock.POLLS_TO_TERMINAL}",
    )
    c.check(
        "stops polling once the operation is terminal",
        len(polls) == mock.POLLS_TO_TERMINAL,
        f"got {len(polls)} polls; terminal state is first returned on poll "
        f"{mock.POLLS_TO_TERMINAL}",
    )
    c.check(
        "every poll uses GET",
        all(e["method"] == "GET" for e in polls),
        "; ".join(sorted({e["method"] for e in polls if e["method"] != "GET"})),
    )
    c.check(
        "every poll carries the ApiKeyAuth header",
        bool(polls) and all(e["headers"].get("authorization") == "NetworkInsight " + mock.TOKENS[0]
                            for e in polls),
        "; ".join(sorted({repr(e["headers"].get("authorization")) for e in polls}))[:300],
    )
    c.check(
        "polls send no request body",
        all(not e["body_raw"] for e in polls),
        "; ".join(str(e["body_raw"]) for e in polls if e["body_raw"])[:200],
    )

    # ---- ordering --------------------------------------------------------
    if bulks and polls:
        c.check(
            "submits before polling",
            bulks[0]["seq"] < min(e["seq"] for e in polls),
            f"submit seq {bulks[0]['seq']}, first poll seq {min(e['seq'] for e in polls)}",
        )
    if auths and bulks:
        c.check(
            "authenticates before submitting",
            auths[0]["seq"] < bulks[0]["seq"],
            f"auth seq {auths[0]['seq']}, submit seq {bulks[0]['seq']}",
        )

    # ---- returned value --------------------------------------------------
    request_id = stdout_value(proc.stdout, "REQUEST_ID")
    c.equal("returns the request_id from the 202 body", request_id, mock.REQUEST_ID)

    c.equal("authenticate returns the token from the response", stdout_value(proc.stdout, "TOKEN"), mock.TOKENS[0])

    raw_result = stdout_value(proc.stdout, "RESULT")
    if c.check("harness printed a RESULT line", raw_result is not None):
        try:
            result = json.loads(raw_result)
        except Exception as exc:
            c.check("RESULT is valid JSON", False, f"{exc}: {raw_result[:200]}")
            result = None
        if result is not None:
            c.equal("returns the terminal report, not a partial snapshot", result, EXPECTED_RESULT)

    c.equal("domain authenticate returns and holds the replacement token",
            stdout_value(proc.stdout, "SVC_TOKEN"), mock.TOKENS[1])
    c.equal("partial-domain authenticate returns and holds its token",
            stdout_value(proc.stdout, "LOCAL_TOKEN"), mock.TOKENS[2])

    # ---- bounded timeout -------------------------------------------------
    timeout_log_path = os.path.join(workdir, "timeout-requests.jsonl")
    timeout_server, _timeout_thread, timeout_port = mock.start_server(
        timeout_log_path, never_terminal=True)
    timeout_proc = None
    timed_out = False
    try:
        timeout_proc = run_harness(
            build_dir,
            f"http://127.0.0.1:{timeout_port}",
            "--timeout",
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        timeout_server.shutdown()
        timeout_server.server_close()

    c.check(
        "bounded polling returns control before the verifier deadline",
        not timed_out,
        "timeout scenario was still running after 10 seconds",
    )
    if timeout_proc is not None:
        c.check(
            "nonterminal operation raises VcfOpsNetworksException after its configured timeout",
            timeout_proc.returncode == 0 and "TIMEOUT_OK" in timeout_proc.stdout,
            f"exit {timeout_proc.returncode}; stdout={timeout_proc.stdout!r}; stderr={timeout_proc.stderr!r}",
        )

    print()
    return c.report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"verifier error: {exc}")
        sys.exit(2)
