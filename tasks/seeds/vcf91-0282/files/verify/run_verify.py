#!/usr/bin/env python3
"""Protected verification for the VCF Operations 9.1 incident-diagnosis client.

Runs entirely against the loopback mock in mock/vcf_ops_mock.py. No VMware endpoint,
and no network at all, is contacted.

  1. checksum the protected files (contract, provenance, mock, harness, this script's rules)
  2. compile src/VcfOpsClient.java together with harness/TestMain.java
  3. start the mock on 127.0.0.1 with an ephemeral port
  4. run TestMain against it
  5. assert the exact request wire shape recorded in the mock's request log
  6. assert docs/diagnosis.md cites the evidence that only the API walk could produce

Usage: python3 verify/run_verify.py [--root DIR] [--keep]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = "/suite-api"
SCHEME = "vRealizeOpsToken"
DATASTORE = "wld01-vsan-ds01"
USER = "svc-diag"
PASS = "R3d-Herring!2026"

OPERATION_IDS = [
    "acquireToken",
    "getMatchingResources",
    "queryAlert",
    "getAlertContributingSymptoms",
    "getSymptoms",
    "getTasksStatus",
    "releaseToken",
]

class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        self.checks += 1
        if ok:
            print("  ok   %s" % label)
        else:
            msg = label if not detail else "%s\n         %s" % (label, detail)
            print("  FAIL %s" % msg)
            self.failures.append(label)
        return ok

    def eq(self, actual, expected, label: str) -> bool:
        return self.check(
            actual == expected,
            label,
            "expected: %r\n         actual:   %r" % (expected, actual),
        )


# ---------------------------------------------------------------------------
# step 1 - protected files
# ---------------------------------------------------------------------------

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_protected(root: Path, rep: Report) -> None:
    print("[1/6] protected files")
    manifest_path = root / "verify" / "protected_manifest.json"
    if not rep.check(manifest_path.is_file(), "verify/protected_manifest.json is present"):
        return
    manifest = json.loads(manifest_path.read_text())
    for rel, want in sorted(manifest["sha256"].items()):
        p = root / rel
        if not rep.check(p.is_file(), "%s is present" % rel):
            continue
        rep.eq(sha256(p), want, "%s is unmodified" % rel)


# ---------------------------------------------------------------------------
# step 2 - shape of the deliverable
# ---------------------------------------------------------------------------


def check_layout(root: Path, rep: Report) -> None:
    print("[2/6] deliverable layout")
    src = root / "src"
    rep.check((src / "VcfOpsClient.java").is_file(), "src/VcfOpsClient.java exists")
    extras = sorted(p.name for p in src.rglob("*") if p.is_file() and p.name != "VcfOpsClient.java")
    rep.eq(extras, [], "src/ holds the single-file client and nothing else")
    body = (src / "VcfOpsClient.java").read_text(errors="replace") if (src / "VcfOpsClient.java").is_file() else ""
    rep.check(
        "UnsupportedOperationException(\"VcfOpsClient is not implemented yet\")" not in body,
        "src/VcfOpsClient.java is no longer the stub",
    )


# ---------------------------------------------------------------------------
# step 3/4 - compile and run
# ---------------------------------------------------------------------------


def compile_and_run(root: Path, work: Path, rep: Report) -> bool:
    print("[3/6] compile")
    classes = work / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    javac = shutil.which("javac")
    if not rep.check(javac is not None, "javac is available"):
        return False
    proc = subprocess.run(
        [javac, "-nowarn", "-d", str(classes), str(root / "src" / "VcfOpsClient.java"),
         str(root / "harness" / "TestMain.java")],
        capture_output=True,
        text=True,
    )
    if not rep.check(proc.returncode == 0, "client and harness compile", proc.stderr.strip()[:4000]):
        return False

    print("[4/6] run harness against the loopback mock")
    portfile = work / "port"
    if portfile.exists():
        portfile.unlink()
    mock = subprocess.Popen(
        [sys.executable, str(root / "mock" / "vcf_ops_mock.py"),
         "--logdir", str(work), "--port", "0", "--portfile", str(portfile)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port = None
        deadline = time.time() + 20
        while time.time() < deadline:
            if mock.poll() is not None:
                break
            if portfile.is_file():
                txt = portfile.read_text().strip()
                if txt.isdigit():
                    port = int(txt)
                    break
            time.sleep(0.05)
        if not rep.check(port is not None, "mock started on 127.0.0.1"):
            return False

        base_url = "http://127.0.0.1:%d%s" % (port, BASE)
        evidence = work / "evidence.json"
        java = shutil.which("java")
        if not rep.check(java is not None, "java is available"):
            return False
        run = subprocess.run(
            [java, "-cp", str(classes), "TestMain", base_url, str(evidence)],
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "http_proxy": "", "https_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": ""},
        )
        ok = rep.check(
            run.returncode == 0,
            "TestMain completes the incident walk",
            (run.stdout[-2000:] + "\n" + run.stderr[-4000:]).strip(),
        )
        rep.check(evidence.is_file(), "harness wrote the evidence file")
        return ok
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            mock.kill()


# ---------------------------------------------------------------------------
# step 5 - wire shape
# ---------------------------------------------------------------------------


def load_log(work: Path) -> list[dict]:
    p = work / "requests.jsonl"
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def keys(obj) -> list[str]:
    return sorted(obj) if isinstance(obj, dict) else ["<not-an-object>"]


def has_unset_field_value(node, *, root=False) -> bool:
    """True if a body field is null or an empty string/array/object.

    The root object may itself be empty: some required request bodies contain only
    optional fields, so an all-unsupplied call is represented correctly by ``{}``.
    """
    if node is None:
        return True
    if isinstance(node, dict):
        return (not root and not node) or any(has_unset_field_value(v) for v in node.values())
    if isinstance(node, list):
        return not node or any(has_unset_field_value(v) for v in node)
    if isinstance(node, str):
        return node == ""
    return False


def check_wire(work: Path, rep: Report) -> None:
    print("[5/6] request wire shape")
    log = load_log(work)
    state = json.loads((work / "state.json").read_text())

    if not rep.eq(len(log), 18, "the client issued exactly the 18 expected requests"):
        for r in log:
            print("         seq %-3s %-6s %s?%s -> %s" % (r["seq"], r["method"], r["path"], r["rawQuery"], r["status"]))
        return

    rep.eq([r["status"] for r in log], [200] * 18, "every request was accepted by the mock")

    tokens = state["tokensIssued"]
    if not rep.eq(len(tokens), 2, "exactly two sessions were opened"):
        return
    tok1, tok2 = tokens[0]["token"], tokens[1]["token"]
    rep.eq([t["released"] for t in tokens], [True, True], "both sessions were released")
    ds = state["datastoreResourceId"]
    alert = state["capacityAlertId"]
    task = state["notificationTaskId"]

    def hdr(r, name):
        return r["headers"].get(name)

    def expect(i, method, path):
        r = log[i]
        rep.eq((r["method"], r["path"]), (method, BASE + path), "seq %d is %s %s" % (i, method, path))
        return r

    def expect_auth(r, tok, i):
        rep.eq(hdr(r, "authorization"), "%s %s" % (SCHEME, tok), "seq %d carries the live session token" % i)

    def expect_accept(r, i):
        rep.check(
            (hdr(r, "accept") or "").split(";")[0].strip() == "application/json",
            "seq %d sends Accept: application/json" % i,
            "actual: %r" % hdr(r, "accept"),
        )

    def expect_json_ct(r, i):
        rep.check(
            (hdr(r, "content-type") or "").split(";")[0].strip() == "application/json",
            "seq %d sends Content-Type: application/json" % i,
            "actual: %r" % hdr(r, "content-type"),
        )

    def expect_no_body(r, i):
        rep.eq(r["bodyBytes"], 0, "seq %d sends no request payload" % i)

    def expect_no_query(r, i):
        rep.eq(r["rawQuery"], "", "seq %d sends no query string" % i)

    # These rules apply to every operation, not just the diagnostic-walk samples
    # asserted in detail below.
    for i, r in enumerate(log):
        expect_accept(r, i)
        if i in (0, 7):
            rep.check(hdr(r, "authorization") is None, "seq %d acquireToken is unauthenticated" % i,
                      "actual: %r" % hdr(r, "authorization"))
        else:
            expect_auth(r, tok1 if i < 7 else tok2, i)

        if i in (0, 1, 2, 7, 8, 9, 10, 11):
            expect_json_ct(r, i)
        else:
            rep.check(hdr(r, "content-type") is None,
                      "seq %d bodyless operation sends no Content-Type" % i,
                      "actual: %r" % hdr(r, "content-type"))
            expect_no_body(r, i)

    # ---- phase 1: every optional argument unsupplied ------------------------
    r = expect(0, "POST", "/api/auth/token/acquire")
    expect_no_query(r, 0)
    rep.eq(keys(r["bodyJson"]), ["password", "username"],
           "seq 0 body omits the unset optional authSource")
    rep.eq((r["bodyJson"] or {}).get("username"), USER, "seq 0 sends the username")
    rep.eq((r["bodyJson"] or {}).get("password"), PASS, "seq 0 sends the password")

    r = expect(1, "POST", "/api/resources/query")
    expect_no_query(r, 1)
    rep.eq(keys(r["bodyJson"]), ["name"], "seq 1 body omits the unset optional resourceKind")
    rep.eq((r["bodyJson"] or {}).get("name"), [DATASTORE], "seq 1 filters on the datastore name")

    r = expect(2, "POST", "/api/alerts/query")
    expect_no_query(r, 2)
    b = r["bodyJson"] or {}
    rep.eq(keys(b), ["activeOnly", "alertCriticality", "resource-query"],
           "seq 2 body carries exactly the supplied alert-query fields")
    rep.eq(b.get("activeOnly"), True, "seq 2 sends activeOnly as a JSON boolean")
    rep.eq(b.get("alertCriticality"), ["CRITICAL", "IMMEDIATE"], "seq 2 sends the criticality filter")
    rep.eq(keys(b.get("resource-query")), ["resourceId"],
           "seq 2 nests the resource filter under the hyphenated 'resource-query' key")
    rep.eq((b.get("resource-query") or {}).get("resourceId"), [ds],
           "seq 2 scopes to the datastore id returned by seq 1")

    r = expect(3, "GET", "/api/alerts/contributingsymptoms")
    expect_no_body(r, 3)
    rep.eq(r["query"], {"id": [alert]}, "seq 3 asks for the capacity alert returned by seq 2")

    r = expect(4, "GET", "/api/symptoms")
    expect_no_body(r, 4)
    rep.eq(r["query"], {"resourceId": [ds], "activeOnly": ["true"]},
           "seq 4 omits the unset includeAlarmInfo, page and pageSize parameters")

    r = expect(5, "GET", "/api/tasks")
    expect_no_body(r, 5)
    rep.eq(r["query"], {"taskState": ["ERROR"]}, "seq 5 omits the unset taskId parameter")

    r = expect(6, "POST", "/api/auth/token/release")
    expect_no_query(r, 6)
    expect_no_body(r, 6)

    # ---- phase 2: the same optional arguments supplied ---------------------
    r = expect(7, "POST", "/api/auth/token/acquire")
    rep.eq(keys(r["bodyJson"]), ["authSource", "password", "username"],
           "seq 7 sends authSource once the caller supplies it")
    rep.eq((r["bodyJson"] or {}).get("authSource"), "local", "seq 7 sends the supplied auth source")

    r = expect(8, "POST", "/api/resources/query")
    rep.eq(keys(r["bodyJson"]), ["name", "resourceKind"], "seq 8 sends resourceKind once supplied")
    rep.eq((r["bodyJson"] or {}).get("resourceKind"), ["Datastore"], "seq 8 sends the supplied resource kind")

    r = expect(9, "POST", "/api/resources/query")
    rep.eq(r["bodyJson"], {}, "seq 9 represents all-unsupplied resource filters with an empty root object")

    r = expect(10, "POST", "/api/alerts/query")
    b = r["bodyJson"] or {}
    rep.eq(keys(b), ["activeOnly", "resource-query"], "seq 10 omits an empty alertCriticality list")
    rep.eq(b.get("activeOnly"), False, "seq 10 still sends activeOnly when it is false")
    rep.eq((b.get("resource-query") or {}).get("resourceId"), [ds], "seq 10 keeps the resource filter")

    r = expect(11, "POST", "/api/alerts/query")
    b = r["bodyJson"] or {}
    rep.eq(keys(b), ["activeOnly", "alertCriticality"],
           "seq 11 drops the whole 'resource-query' object for an empty resource-id list")
    rep.eq(b.get("alertCriticality"), ["CRITICAL"], "seq 11 sends the supplied criticality filter")

    r = expect(12, "GET", "/api/alerts/contributingsymptoms")
    rep.eq(r["query"], {"id": [alert, alert]},
           "seq 12 serialises an array query as repeated form keys")

    r = expect(13, "GET", "/api/symptoms")
    rep.eq(r["query"], {"resourceId": [ds], "activeOnly": ["false"], "includeAlarmInfo": ["true"]},
           "seq 13 sends activeOnly=false and includeAlarmInfo once supplied")

    r = expect(14, "GET", "/api/symptoms")
    expect_no_query(r, 14)

    r = expect(15, "GET", "/api/tasks")
    rep.eq(r["query"], {"taskState": ["ERROR"], "taskId": [task]},
           "seq 15 sends the taskId discovered in seq 5")

    r = expect(16, "GET", "/api/tasks")
    expect_no_query(r, 16)

    r = expect(17, "POST", "/api/auth/token/release")
    expect_no_body(r, 17)

    offenders = [
        "seq %d: %s" % (r["seq"], r["bodyRaw"])
        for r in log
        if r["bodyBytes"] and has_unset_field_value(r["bodyJson"], root=True)
    ]
    rep.eq(offenders, [], "no request body serialises an unset field as null, \"\", [] or {}")


# ---------------------------------------------------------------------------
# step 6 - the diagnosis
# ---------------------------------------------------------------------------


def iso_utc(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def check_diagnosis(root: Path, work: Path, rep: Report) -> None:
    print("[6/6] diagnosis report")
    p = root / "docs" / "diagnosis.md"
    if not rep.check(p.is_file(), "docs/diagnosis.md exists"):
        return
    text = p.read_text(errors="replace")
    flat = " ".join(text.lower().split())

    evidence_path = work / "evidence.json"
    if not rep.check(evidence_path.is_file(), "harness evidence is available for diagnosis checks"):
        return
    evidence = json.loads(evidence_path.read_text())
    resources = json.loads(evidence["resources"])
    alerts = json.loads(evidence["alerts"])
    contributing = json.loads(evidence["contributingSymptoms"])
    symptoms = json.loads(evidence["symptoms"])
    tasks = json.loads(evidence["tasks"])

    alert = next(a for a in alerts["alerts"] if a["alertId"] == evidence["alertId"])
    rep.check(alert["alertDefinitionName"].lower() in flat,
              "diagnosis quotes the alert definition name")

    groups = contributing["contributingSymptoms"]
    group = next(g for g in groups if g["alertId"] == alert["alertId"])
    symptom_ids = {
        s["symptomId"]
        for s in group["contributingSymptoms"]["contributingSymptoms"]
    }
    contributing_symptoms = [s for s in symptoms["symptom"] if s["id"] in symptom_ids]
    rep.eq(len(contributing_symptoms), len(symptom_ids),
           "the symptom-detail response covers every contributing symptom")
    for symptom in contributing_symptoms:
        stat_key = symptom["statKey"]
        rep.check(stat_key.lower() in flat,
                  "diagnosis quotes contributing statKey %r" % stat_key)
        rep.check(symptom["message"].lower() in flat,
                  "diagnosis quotes the message for %r" % stat_key)
        timestamp_forms = (str(symptom["startTimeUTC"]), iso_utc(symptom["startTimeUTC"]).lower())
        rep.check(any(value in flat for value in timestamp_forms),
                  "diagnosis quotes the start timestamp for %r" % stat_key)

    resource = next(r for r in resources["resourceList"] if r["identifier"] == evidence["datastoreId"])
    collection = resource["resourceStatusStates"][0]
    rep.check(str(collection["resourceStatus"]).lower() in flat,
              "diagnosis cites the resource collection status")
    rejection_terms = ("wrong", "false", "contradict", "rules out", "did not stop", "never interrupted",
                       "no collection gap")
    rep.check(any(term in flat for term in rejection_terms),
              "diagnosis explicitly rejects the stopped-collection theory")

    notification = next(
        t for t in tasks["taskStatusList"]
        if any(alert["alertId"] in msg for msg in t.get("errorMessages", []))
    )
    error_text = " ".join(notification["errorMessages"]).lower()
    rep.check("smtp" in error_text and "smtp" in flat and ("timeout" in flat or "timed out" in flat),
              "diagnosis explains the SMTP timeout")
    rep.check("dropped" in error_text and "notification" in flat and "drop" in flat,
              "diagnosis explains that notifications were dropped")

    missing = [o for o in OPERATION_IDS if o.lower() not in flat]
    rep.eq(missing, [], "diagnosis names every operationId it relied on")


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--keep", action="store_true", help="keep the .work directory")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    work = root / ".work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    rep = Report()
    check_protected(root, rep)
    check_layout(root, rep)
    ran = compile_and_run(root, work, rep)
    if ran:
        check_wire(work, rep)
    else:
        print("[5/6] request wire shape\n  FAIL skipped: the harness did not complete")
        rep.failures.append("wire shape not verified")
    check_diagnosis(root, work, rep)

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if rep.failures:
        print("VERIFY FAILED: %d of %d checks failed" % (len(rep.failures), rep.checks))
        for f in rep.failures:
            print("  - %s" % f)
        return 1
    print("VERIFY PASSED: %d checks" % rep.checks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
