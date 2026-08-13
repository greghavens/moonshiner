#!/usr/bin/env python3
"""Protected verification for the VCF Operations alert triage module.

For each scenario it starts mock/vcfops_mock.py on a loopback port, runs the
triage module through verification/harness/Invoke-TriageRun.ps1, then asserts
the exact wire shape of every request the module made, using the mock's request
log. No VMware endpoint is contacted.

    python3 verification/run_verification.py [--case token-expiry] [--keep]

Exit code 0 means every check passed.
"""

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text())
SDK_MODULE = "VMware.Sdk.Vcf.Ops"
SDK_USER_AGENT_PREFIX = "VMware.Sdk.Vcf.Ops/"
CONNECT_USER_AGENTS = ("PowerCLI", SDK_USER_AGENT_PREFIX)
SUMMARY_KEYS = {
    "tokenAcquisitions",
    "pagesRead",
    "alertsSeen",
    "batchesSubmitted",
    "batchesRetried",
    "assignedAlertIds",
    "suspendedAlertIds",
}

C = "7b8d5c10-000%d-4c00-9a00-00000000000%d"
I = "7b8d5c10-000%d-4900-9a00-00000000000%d"
CRIT = [C % (n, n) for n in (1, 2, 3)]
IMMED = [I % (n, n) for n in (4, 5, 6)]
WARN = ["9e2a4b70-0001-4200-8e00-000000000011", "9e2a4b70-0002-4200-8e00-000000000012"]


class Failure(Exception):
    pass


def op(*ids):
    return list(ids)


CASES = {
    "token-expiry": {
        "scenario": "token-expiry.json",
        "config": "triage-run.json",
        "sequence": op(
            "acquireToken",
            "getCurrentVersionOfServer",
            "queryAlert",
            "queryAlert",
            "queryAlert",
            "modifyAlerts",
            "modifyAlerts",
            "modifyAlerts",
            "acquireToken",
            "getCurrentVersionOfServer",
            "modifyAlerts",
            "modifyAlerts",
            "releaseToken",
        ),
        "unauthorized": 1,
        "pages": [0, 1, 2],
        "batches": [
            ("assignownership", CRIT[0:2]),
            ("assignownership", CRIT[2:3]),
            ("suspend", IMMED[0:2]),
            ("suspend", IMMED[0:2]),
            ("suspend", IMMED[2:3]),
        ],
        "summary": {
            "tokenAcquisitions": 2,
            "pagesRead": 3,
            "alertsSeen": 6,
            "batchesSubmitted": 4,
            "batchesRetried": 1,
            "assignedAlertIds": CRIT,
            "suspendedAlertIds": IMMED,
        },
    },
    "steady-token": {
        "scenario": "steady-token.json",
        "config": "triage-run.json",
        "sequence": op(
            "acquireToken",
            "getCurrentVersionOfServer",
            "queryAlert",
            "queryAlert",
            "queryAlert",
            "modifyAlerts",
            "modifyAlerts",
            "modifyAlerts",
            "modifyAlerts",
            "releaseToken",
        ),
        "unauthorized": 0,
        "pages": [0, 1, 2],
        "batches": [
            ("assignownership", CRIT[0:2]),
            ("assignownership", CRIT[2:3]),
            ("suspend", IMMED[0:2]),
            ("suspend", IMMED[2:3]),
        ],
        "summary": {
            "tokenAcquisitions": 1,
            "pagesRead": 3,
            "alertsSeen": 6,
            "batchesSubmitted": 4,
            "batchesRetried": 0,
            "assignedAlertIds": CRIT,
            "suspendedAlertIds": IMMED,
        },
    },
    "unfiltered-query": {
        "scenario": "unfiltered-query.json",
        "config": "triage-minimal.json",
        "sequence": op(
            "acquireToken",
            "getCurrentVersionOfServer",
            "queryAlert",
            "modifyAlerts",
            "releaseToken",
        ),
        "unauthorized": 0,
        "pages": [0],
        "batches": [("suspend", WARN)],
        "summary": {
            "tokenAcquisitions": 1,
            "pagesRead": 1,
            "alertsSeen": 2,
            "batchesSubmitted": 1,
            "batchesRetried": 0,
            "assignedAlertIds": [],
            "suspendedAlertIds": WARN,
        },
    },
}


# --------------------------------------------------------------------------
# running a case
# --------------------------------------------------------------------------
def preflight():
    if shutil.which("pwsh") is None:
        raise Failure("pwsh is not on PATH; PowerShell 7 is an environment prerequisite")
    probe = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "if (Get-Module -ListAvailable -Name %s) { 'yes' } else { 'no' }" % SDK_MODULE,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if "yes" not in probe.stdout:
        raise Failure(
            "%s is not installed. It is an environment prerequisite: "
            "Install-PSResource -Name %s -TrustRepository" % (SDK_MODULE, SDK_MODULE)
        )


def start_mock(scenario, workdir):
    ready = workdir / "ready.json"
    log = workdir / "requests.jsonl"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "mock" / "vcfops_mock.py"),
            "--contract", str(ROOT / "docs" / "contract.json"),
            "--scenario", str(ROOT / "mock" / "scenarios" / scenario),
            "--host", "127.0.0.1",
            "--port", "0",
            "--log", str(log),
            "--ready-file", str(ready),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if ready.exists():
            return proc, json.loads(ready.read_text())["port"], log
        if proc.poll() is not None:
            raise Failure("mock exited early:\n%s" % proc.stdout.read())
        time.sleep(0.05)
    proc.kill()
    raise Failure("mock did not start listening within 30s")


def run_case(name, case, keep):
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="vcfops-%s-" % name))
    proc, port, log_path = start_mock(case["scenario"], workdir)
    try:
        config = json.loads((ROOT / "config" / case["config"]).read_text())
        config["port"] = port
        config_path = workdir / "run-config.json"
        config_path.write_text(json.dumps(config, indent=2))
        summary_path = workdir / "summary.json"

        env = dict(os.environ)
        env.setdefault("POWERSHELL_TELEMETRY_OPTOUT", "1")
        result = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File", str(ROOT / "verification" / "harness" / "Invoke-TriageRun.ps1"),
                "-ConfigPath", str(config_path),
                "-OutputPath", str(summary_path),
            ],
            capture_output=True,
            text=True,
            timeout=900,
            env=env,
            cwd=str(ROOT),
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    payload = {
        "name": name,
        "case": case,
        "config": config,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "summary_path": summary_path,
        "entries": entries,
        "workdir": workdir,
    }
    if not keep:
        payload["cleanup"] = workdir
    return payload


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def check_driver(run):
    if run["returncode"] != 0:
        raise Failure(
            "Invoke-TriageRun.ps1 exited %d\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (run["returncode"], run["stdout"].strip(), run["stderr"].strip())
        )


def check_summary(run):
    path = run["summary_path"]
    if not path.exists():
        raise Failure("the module produced no summary object")
    summary = json.loads(path.read_text())
    if not isinstance(summary, dict):
        raise Failure("the triage summary must be an object, got %s" % type(summary).__name__)
    keys = set(summary)
    if keys != SUMMARY_KEYS:
        raise Failure(
            "summary keys are %s, expected exactly %s"
            % (sorted(keys), sorted(SUMMARY_KEYS))
        )
    expected = run["case"]["summary"]
    for key in ("tokenAcquisitions", "pagesRead", "alertsSeen", "batchesSubmitted", "batchesRetried"):
        if summary[key] != expected[key]:
            raise Failure("summary.%s is %r, expected %r" % (key, summary[key], expected[key]))
    for key in ("assignedAlertIds", "suspendedAlertIds"):
        got = [str(v).lower() for v in as_list(summary[key])]
        if got != [v.lower() for v in expected[key]]:
            raise Failure("summary.%s is %r, expected %r" % (key, got, expected[key]))
    run["summary"] = summary


def check_only_contracted_operations(run):
    for entry in run["entries"]:
        if entry["operationId"] is None:
            raise Failure(
                "request %d (%s %s) hit an operation that the contract does not name"
                % (entry["seq"], entry["method"], entry["path"])
            )
        contracted = CONTRACT["operations"][entry["operationId"]]
        if entry["path"] != contracted["requestPath"] or entry["method"] != contracted["method"]:
            raise Failure("request %d does not match the contracted route" % entry["seq"])


def check_sequence(run):
    got = [e["operationId"] for e in run["entries"]]
    want = run["case"]["sequence"]
    if got != want:
        raise Failure("request sequence was\n  %s\nexpected\n  %s" % (got, want))


def check_statuses(run):
    unauthorized = [e for e in run["entries"] if e["status"] == 401]
    others = [e for e in run["entries"] if e["status"] not in (200, 401)]
    if others:
        first = others[0]
        raise Failure(
            "request %d (%s) was rejected with %d: %s"
            % (first["seq"], first["operationId"], first["status"], first.get("error"))
        )
    if len(unauthorized) != run["case"]["unauthorized"]:
        raise Failure(
            "expected %d rejected-token response(s), saw %d"
            % (run["case"]["unauthorized"], len(unauthorized))
        )


def check_sdk_made_the_calls(run):
    for entry in run["entries"]:
        agent = entry.get("userAgent") or ""
        if entry["operationId"] == "acquireToken":
            if not agent.startswith(CONNECT_USER_AGENTS):
                raise Failure(
                    "request %d was not issued by the PowerCLI SDK (User-Agent %r)"
                    % (entry["seq"], agent)
                )
            continue
        if not agent.startswith(SDK_USER_AGENT_PREFIX):
            raise Failure(
                "request %d (%s) was not issued by %s (User-Agent %r); the module must drive "
                "the API through the SDK cmdlets rather than hand-rolled HTTP calls"
                % (entry["seq"], entry["operationId"], SDK_MODULE, agent)
            )


def check_authorization(run):
    prefix = CONTRACT["securityScheme"]["valuePrefix"].strip()
    issued = []
    for entry in run["entries"]:
        operation = CONTRACT["operations"][entry["operationId"]]
        if operation["authorization"] == "issues-token":
            if entry["authorizationScheme"] is not None:
                raise Failure("request %d must not present an access token" % entry["seq"])
            issued.append("ops-token-%d" % (len(issued) + 1))
            continue
        if entry["authorizationScheme"] != prefix:
            raise Failure(
                "request %d used authorization scheme %r, expected %r"
                % (entry["seq"], entry["authorizationScheme"], prefix)
            )
        if entry["tokenId"] != issued[-1]:
            raise Failure(
                "request %d used token %r but the most recent token is %r; the module must use "
                "the refreshed token for everything after a refresh"
                % (entry["seq"], entry["tokenId"], issued[-1])
            )


def check_acquire_bodies(run):
    config = run["config"]
    expected = {"username": config["user"], "password": config["password"]}
    if config.get("authSource"):
        expected["authSource"] = config["authSource"]
    for entry in run["entries"]:
        if entry["operationId"] != "acquireToken":
            continue
        body = entry["requestBody"]
        if set(body) != set(expected):
            raise Failure(
                "request %d sent username-password keys %s, expected exactly %s"
                % (entry["seq"], sorted(body), sorted(expected))
            )
        for key, value in expected.items():
            if body[key] != value:
                raise Failure("request %d sent %s=%r, expected %r" % (entry["seq"], key, body[key], value))
        if entry["queryString"]:
            raise Failure("request %d must not carry a query string" % entry["seq"])


def check_query_alert_requests(run):
    config = run["config"]
    wanted_body = dict(config["query"])
    queries = [e for e in run["entries"] if e["operationId"] == "queryAlert"]
    pages = run["case"]["pages"]
    if len(queries) != len(pages):
        raise Failure("expected %d alert-query page(s), saw %d" % (len(pages), len(queries)))
    for entry, page in zip(queries, pages):
        keys = set(entry["query"])
        if keys != {"page", "pageSize"}:
            raise Failure(
                "request %d carried query parameters %s, expected exactly ['page', 'pageSize']"
                % (entry["seq"], sorted(keys))
            )
        if entry["query"]["page"] != str(page):
            raise Failure(
                "request %d asked for page %s, expected %d" % (entry["seq"], entry["query"]["page"], page)
            )
        if entry["query"]["pageSize"] != str(config["pageSize"]):
            raise Failure(
                "request %d used pageSize %s, expected %d"
                % (entry["seq"], entry["query"]["pageSize"], config["pageSize"])
            )
        body = entry["requestBody"]
        if set(body) != set(wanted_body):
            raise Failure(
                "request %d sent alert-query fields %s, expected exactly %s -- every filter the run "
                "configuration does not set has to be absent from the body"
                % (entry["seq"], sorted(body), sorted(wanted_body))
            )
        for key, value in wanted_body.items():
            if body[key] != value:
                raise Failure("request %d sent alert-query %s=%r, expected %r" % (entry["seq"], key, body[key], value))


def check_modify_alerts_requests(run):
    config = run["config"]
    batch_size = config["batchSize"]
    expected = run["case"]["batches"]
    entries = [e for e in run["entries"] if e["operationId"] == "modifyAlerts"]
    if len(entries) != len(expected):
        raise Failure("expected %d modifyAlerts request(s), saw %d" % (len(expected), len(entries)))

    for entry, (action, alert_ids) in zip(entries, expected):
        query = entry["query"]
        if query.get("action") != action:
            raise Failure("request %d used action %r, expected %r" % (entry["seq"], query.get("action"), action))
        if action == "assignownership":
            want_keys = {"action", "userAccountID"}
            if query.get("userAccountID") != config["assignOwnership"]["userAccountId"]:
                raise Failure(
                    "request %d sent userAccountID=%r, expected %r"
                    % (entry["seq"], query.get("userAccountID"), config["assignOwnership"]["userAccountId"])
                )
        else:
            want_keys = {"action", "minutes"}
            if query.get("minutes") != str(config["suspend"]["minutes"]):
                raise Failure(
                    "request %d sent minutes=%r, expected %r"
                    % (entry["seq"], query.get("minutes"), str(config["suspend"]["minutes"]))
                )
        if set(query) != want_keys:
            raise Failure(
                "request %d carried query parameters %s, expected exactly %s -- optional parameters "
                "that do not apply to this action must be omitted, not sent empty"
                % (entry["seq"], sorted(query), sorted(want_keys))
            )
        body = entry["requestBody"]
        if set(body) != {"uuids"}:
            raise Failure("request %d sent uuid-values keys %s, expected ['uuids']" % (entry["seq"], sorted(body)))
        got = [str(v).lower() for v in body["uuids"]]
        if got != [v.lower() for v in alert_ids]:
            raise Failure("request %d acted on %s, expected %s" % (entry["seq"], got, alert_ids))
        if len(got) > batch_size:
            raise Failure("request %d carried %d alerts, over the configured batch size of %d" % (entry["seq"], len(got), batch_size))


def check_work_is_not_replayed(run):
    """Every alert must be acted on exactly once by a request the server accepted."""
    applied = {}
    for entry in run["entries"]:
        if entry["operationId"] != "modifyAlerts" or entry["status"] != 200:
            continue
        for alert_id in entry["requestBody"]["uuids"]:
            key = str(alert_id).lower()
            if key in applied:
                raise Failure(
                    "alert %s was acted on twice (requests %d and %d); a token refresh must not "
                    "replay work that already succeeded" % (key, applied[key], entry["seq"])
                )
            applied[key] = entry["seq"]
    expected = {v.lower() for v in run["case"]["summary"]["assignedAlertIds"] + run["case"]["summary"]["suspendedAlertIds"]}
    if set(applied) != expected:
        missing = sorted(expected - set(applied))
        extra = sorted(set(applied) - expected)
        raise Failure("alerts acted on do not match; missing=%s unexpected=%s" % (missing, extra))


def check_refresh_replays_only_the_failed_call(run):
    entries = run["entries"]
    for index, entry in enumerate(entries):
        if entry["status"] != 401:
            continue
        tail = entries[index + 1:]
        if not tail or tail[0]["operationId"] != "acquireToken":
            raise Failure(
                "request %d was refused for an expired token but the module did not acquire a new "
                "one next" % entry["seq"]
            )
        replay = [
            e for e in tail
            if e["operationId"] == entry["operationId"] and e["requestBody"] == entry["requestBody"]
        ]
        if not replay:
            raise Failure(
                "the call refused at request %d was never replayed after the refresh; the pending "
                "work was lost" % entry["seq"]
            )
        if replay[0]["queryString"] != entry["queryString"]:
            raise Failure(
                "request %d was replayed with a different query string (%r vs %r)"
                % (entry["seq"], replay[0]["queryString"], entry["queryString"])
            )
        if replay[0]["status"] != 200:
            raise Failure("the replay of request %d was not accepted" % entry["seq"])


def check_release_is_last(run):
    entries = run["entries"]
    releases = [e for e in entries if e["operationId"] == "releaseToken"]
    if len(releases) != 1:
        raise Failure("expected exactly one releaseToken call, saw %d" % len(releases))
    if releases[0]["seq"] != entries[-1]["seq"]:
        raise Failure("releaseToken must be the last request of the run")
    if releases[0]["requestBodyRaw"].strip():
        raise Failure("releaseToken must not carry a request body")


CHECKS = [
    ("driver exits cleanly", check_driver),
    ("summary object matches the run", check_summary),
    ("only contracted operations are called", check_only_contracted_operations),
    ("request sequence matches the triage policy", check_sequence),
    ("no unexpected error responses", check_statuses),
    ("every request came from the PowerCLI SDK", check_sdk_made_the_calls),
    ("access tokens are presented correctly", check_authorization),
    ("acquireToken bodies carry only the fields in play", check_acquire_bodies),
    ("alert-query omits every filter the run does not set", check_query_alert_requests),
    ("modifyAlerts omits optional parameters that do not apply", check_modify_alerts_requests),
    ("a token refresh does not replay completed work", check_work_is_not_replayed),
    ("a token refresh replays exactly the refused call", check_refresh_replays_only_the_failed_call),
    ("the run ends by releasing the token", check_release_is_last),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", choices=sorted(CASES), help="run a subset of cases")
    ap.add_argument("--keep", action="store_true", help="keep the temporary run directories")
    args = ap.parse_args()

    try:
        preflight()
    except Failure as exc:
        print("PREFLIGHT FAILED: %s" % exc)
        return 2

    failures = 0
    for name in args.case or sorted(CASES):
        print("\n== scenario %s ==" % name)
        try:
            run = run_case(name, CASES[name], args.keep)
        except Failure as exc:
            print("  FAIL  could not run the case: %s" % exc)
            failures += 1
            continue
        if args.keep:
            print("  run directory: %s" % run["workdir"])
        for label, check in CHECKS:
            try:
                check(run)
            except Failure as exc:
                print("  FAIL  %s\n        %s" % (label, str(exc).replace("\n", "\n        ")))
                failures += 1
                break
            except Exception as exc:  # noqa: BLE001 - a malformed run must not crash the report
                print("  FAIL  %s\n        unexpected %s: %s" % (label, type(exc).__name__, exc))
                failures += 1
                break
            else:
                print("  ok    %s" % label)
        if not args.keep:
            shutil.rmtree(run["workdir"], ignore_errors=True)

    print("")
    if failures:
        print("VERIFICATION FAILED (%d failing check%s)" % (failures, "" if failures == 1 else "s"))
        return 1
    print("VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
