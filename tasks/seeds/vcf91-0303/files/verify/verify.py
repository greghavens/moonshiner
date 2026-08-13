#!/usr/bin/env python3
"""Protected verifier for the VCF Operations for Networks application-inventory client.

Reads the artifacts TestMain left behind and checks two things:

  1. The wire shape. Every request the client made is compared against docs/contract.json --
     the exact sequence of operations, the exact set of query keys per request, the exact
     header set, the exact JSON body key set, and the rule that an optional field with no
     configured value is omitted rather than sent empty.

  2. The result. The expected inventory is derived here, independently, from the mock's own
     fixture data plus the ordering rule in README.md, and compared to the client's stdout
     byte for byte.

Expectations are recomputed from the fixtures rather than read from a stored answer file, and
two runs with different datasets, page sizes and terminations are checked, so a client that
hardcodes an answer fails.

No external network access and no VMware endpoint; the mock uses loopback only.

DO NOT MODIFY.
"""

import json
import math
import pathlib
import sys

BASE = "/api/ni"
LIST_PATH = BASE + "/groups/applications"
TOKEN_PATH = BASE + "/auth/token"


class Failure(Exception):
    pass


class Checker:
    def __init__(self):
        self.failures = []
        self.checks = 0
        self.context = ""

    def check(self, condition, message):
        self.checks += 1
        if not condition:
            self.failures.append(f"{self.context}{message}")
        return condition

    def equal(self, actual, expected, message):
        return self.check(
            actual == expected,
            f"{message}\n      expected: {expected!r}\n      actual:   {actual!r}",
        )


def load_jsonl(path):
    if not path.exists():
        raise Failure(f"missing request log: {path}")
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


def expected_cursor(fixture, offset):
    """Return the deterministic opaque token the fixture assigns to a page boundary."""
    return fixture["page_cursors"][str(offset)]


def sort_key(app):
    """The stable order README.md defines: by name, ties broken by entity_id.

    Python's default str ordering matches Java's String.compareTo for the ASCII strings in these
    fixtures.
    """
    return (app["name"], app["entity_id"])


def expected_stdout(apps):
    lines = [f"{a['entity_id']}\t{a['name']}\t{a['tier_count']}" for a in sorted(apps, key=sort_key)]
    lines.append(f"total={len(apps)}")
    return "\n".join(lines) + "\n"


def verify_run(chk, root, run):
    name = run["name"]
    run_dir = root / "out" / name
    chk.context = f"[{name}] "

    fixture = json.loads(
        (root / "harness" / "fixtures" / run["fixture"]).read_text(encoding="utf-8")
    )
    apps = fixture["applications"]
    token = fixture["token"]
    page_size = run["page_size"]
    modified_after = run["modified_after"]

    # --- the client must have succeeded -------------------------------------
    if not chk.check(not run["timed_out"], "the client timed out"):
        return
    stderr = (run_dir / "stderr.txt").read_text(encoding="utf-8") if (run_dir / "stderr.txt").exists() else ""
    if not chk.equal(run["exit_code"], 0, f"the client exited non-zero; stderr was:\n{stderr.rstrip()}\n   "):
        return

    # --- stdout, derived here from the fixture ------------------------------
    actual_stdout = (run_dir / "stdout.txt").read_text(encoding="utf-8")
    chk.equal(actual_stdout, expected_stdout(apps), "stdout does not match the expected inventory")

    # --- the request log ----------------------------------------------------
    log = load_jsonl(run_dir / "requests.jsonl")
    page_count = max(1, math.ceil(len(apps) / page_size))
    expected_total = 1 + page_count + len(apps)

    for entry in log:
        chk.equal(
            entry["status"],
            200,
            f"request #{entry['seq']} ({entry['method']} {entry['raw_path']}) was rejected by the "
            f"mock: {entry.get('rejected_because', '(no reason recorded)')}\n   ",
        )

    if not chk.equal(
        len(log),
        expected_total,
        "wrong number of requests: expected 1 auth + "
        f"{page_count} list page(s) + {len(apps)} detail call(s)\n   "
        + "   observed: "
        + ", ".join(f"{e['method']} {e['raw_path']}{'?' + e['raw_query'] if e['raw_query'] else ''}" for e in log),
    ):
        return

    auth_header = f"NetworkInsight {token}"

    # --- 1. operationId 'create' -------------------------------------------
    req = log[0]
    chk.context = f"[{name}] request #0 (create) "
    chk.equal(req["method"], "POST", "wrong method")
    chk.equal(req["raw_path"], TOKEN_PATH, "wrong path")
    chk.equal(req["raw_query"], "", "the token request takes no query parameters")
    headers = req["headers"]
    chk.check("authorization" not in headers, "must not send an Authorization header")
    chk.equal(
        headers.get("content-type", "").split(";")[0].strip(),
        "application/json",
        "must send Content-Type: application/json",
    )

    try:
        body = json.loads(req["body"])
    except ValueError as exc:
        chk.check(False, f"body is not valid JSON: {exc}")
        body = {}

    expected_keys = ["username", "password"]
    if run["domain_type"] is not None or run["domain_value"] is not None:
        expected_keys.append("domain")
    chk.equal(
        sorted(body.keys()),
        sorted(expected_keys),
        "wrong UserCredential key set -- an optional property with no configured value must be "
        "omitted entirely, not sent as null or empty",
    )
    chk.equal(body.get("username"), run["username"], "wrong username")
    chk.equal(body.get("password"), run["password"], "wrong password")

    if "domain" in expected_keys:
        expected_domain = {}
        if run["domain_type"] is not None:
            expected_domain["domain_type"] = run["domain_type"]
        if run["domain_value"] is not None:
            expected_domain["value"] = run["domain_value"]
        chk.equal(
            body.get("domain"),
            expected_domain,
            "wrong Domain object -- it must carry only the sub-properties that are set",
        )

    # --- 2. operationId 'listApplications' ---------------------------------
    for page in range(page_count):
        req = log[1 + page]
        chk.context = f"[{name}] request #{1 + page} (listApplications page {page}) "
        chk.equal(req["method"], "GET", "wrong method")
        chk.equal(req["raw_path"], LIST_PATH, "wrong path")
        chk.equal(req["headers"].get("authorization"), auth_header, "wrong Authorization header")
        chk.check(
            "content-type" not in req["headers"],
            "a GET with no body must not send a Content-Type header",
        )

        params = req["query_params"]
        expected_params = {"size": str(page_size)}
        if page > 0:
            expected_params["cursor"] = expected_cursor(fixture, page * page_size)
        if modified_after is not None:
            expected_params["modifiedAfter"] = str(modified_after)

        chk.equal(
            sorted(params.keys()),
            sorted(expected_params.keys()),
            "wrong query parameter key set"
            + (
                " -- 'cursor' must be absent on the first request of a walk"
                if page == 0
                else " -- 'cursor' must be sent on every request after the first"
            ),
        )
        for key, value in expected_params.items():
            chk.equal(params.get(key), value, f"wrong value for '{key}'")
        for key, value in params.items():
            chk.check(value != "", f"'{key}' was sent empty; omit the parameter instead")

    # --- 3. operationId 'getApplicationById' -------------------------------
    seen = []
    for index, app in enumerate(apps):
        position = 1 + page_count + index
        req = log[position]
        chk.context = f"[{name}] request #{position} (getApplicationById {index}) "
        chk.equal(req["method"], "GET", "wrong method")
        chk.equal(
            req["raw_path"],
            f"{LIST_PATH}/{app['entity_id']}",
            "wrong path -- detail calls run in list-discovery order and the entity id is sent "
            "verbatim, with ':' left unencoded",
        )
        chk.equal(req["raw_query"], "", "neither optional query parameter is set in this scenario")
        chk.check(
            not req["has_query_delimiter"],
            "the URL must have no '?' at all when there are no query parameters",
        )
        chk.equal(req["headers"].get("authorization"), auth_header, "wrong Authorization header")
        chk.check(
            "content-type" not in req["headers"],
            "a GET with no body must not send a Content-Type header",
        )
        seen.append(req["raw_path"])

    chk.context = f"[{name}] "
    chk.equal(len(set(seen)), len(seen), "the same application was fetched more than once")


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    chk = Checker()

    runs_file = root / "out" / "runs.json"
    if not runs_file.exists():
        print(f"FAIL: {runs_file} is missing -- run ./run.sh first", file=sys.stderr)
        return 1

    runs = json.loads(runs_file.read_text(encoding="utf-8"))["runs"]
    if len(runs) < 2:
        print("FAIL: expected two harness runs", file=sys.stderr)
        return 1

    try:
        for run in runs:
            verify_run(chk, root, run)
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if chk.failures:
        print(f"FAIL: {len(chk.failures)} of {chk.checks} checks failed\n", file=sys.stderr)
        for failure in chk.failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "\nThe wire contract is docs/contract.json; the output format and ordering rule are "
            "in README.md.",
            file=sys.stderr,
        )
        return 1

    print(f"PASS: {chk.checks} checks across {len(runs)} runs")
    print("  - request sequence, query key sets, headers and body shape match docs/contract.json")
    print("  - unset optional fields are omitted rather than sent empty")
    print("  - both paginated collections were walked to exhaustion and emitted in stable order")
    return 0


if __name__ == "__main__":
    sys.exit(main())
