"""Acceptance tests for the dbxjobs package.

Runs a loopback fake Databricks workspace (Jobs API 2.2 subset: runs/submit,
runs/get, runs/get-output, runs/cancel) and drives dbxjobs against it. No real
Databricks, no real credentials, no wall-clock sleeps — waiting is injected
and recorded. The wire contract the fake enforces is pinned in
docs/contract.json. This file and everything under docs/ are protected.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "docs", "contract.json"), "r", encoding="utf-8") as fh:
    CONTRACT = json.load(fh)
with open(os.path.join(HERE, "docs", "official_sources.json"), "r", encoding="utf-8") as fh:
    SOURCES = json.load(fh)

TOKEN = CONTRACT["auth"]["fixture_token"]  # dummy; must never leak
EXPECTED_AUTH = "Bearer " + TOKEN

SUBMIT = "/api/2.2/jobs/runs/submit"
GET_RUN = "/api/2.2/jobs/runs/get"
GET_OUTPUT = "/api/2.2/jobs/runs/get-output"
CANCEL = "/api/2.2/jobs/runs/cancel"

MAX_RETRIES = CONTRACT["rate_limit"]["max_retries"]
POLL_INTERVAL = CONTRACT["test_fixture_notes"]["poll_interval_seconds"]


def envelope(error_code, message):
    return json.dumps({"error_code": error_code, "message": message}).encode()


def task_entry(rid, pos, key, state, result=None):
    entry = {
        "run_id": rid * 10 + pos,
        "task_key": key,
        "attempt_number": 0,
        "status": {"state": state},
        "state": {"life_cycle_state": state},
    }
    if result is not None:
        entry["status"]["termination_details"] = {
            "code": "SUCCESS" if result == "SUCCESS" else "RUN_EXECUTION_ERROR",
            "type": "SUCCESS" if result == "SUCCESS" else "CLIENT_ERROR",
            "message": "",
        }
        entry["state"]["result_state"] = result
    return entry


def run_doc(rid, state, tasks, code=None, ttype=None, message="", result=None):
    """Build a Jobs 2.2 Run object: modern `status` plus deprecated `state`."""
    legacy_lcs = {"PENDING": "PENDING", "QUEUED": "QUEUED", "RUNNING": "RUNNING",
                  "TERMINATING": "TERMINATING", "TERMINATED": "TERMINATED"}[state]
    doc = {
        "run_id": rid,
        "run_page_url": f"https://fixture.example/#job/run/{rid}",
        "status": {"state": state},
        "state": {"life_cycle_state": legacy_lcs, "state_message": message},
        "tasks": tasks,
    }
    if code is not None:
        doc["status"]["termination_details"] = {
            "code": code, "type": ttype, "message": message,
        }
    if result is not None:
        doc["state"]["result_state"] = result
    return doc


class FakeWorkspace:
    """In-memory Jobs API 2.2 subset with request recording and fault injection."""

    def __init__(self):
        self.requests = []       # every request seen, in order
        self.runs = {}           # run_id -> {"seq": [docs], "i": int, "cancel_seq": [...]}
        self.outputs = {}        # task run_id -> output dict
        self.parent_ids = set()
        self.tokens = {}         # idempotency_token -> run_id
        self.pending = []        # scripts for future submits
        self.fail_queue = {}     # path -> [(status, body, headers)] one-shots
        self.always_fail = {}    # path -> (status, body, headers)
        self.cancel_bodies = []
        self.created = 0
        self.next_id = 7000

    def script(self, seq_builder, outputs=None, cancel_seq_builder=None):
        """Queue the lifecycle for the next submitted run. Builders take run_id."""
        self.pending.append((seq_builder, outputs or {}, cancel_seq_builder))

    def fail_once(self, path, status, body, headers=None):
        self.fail_queue.setdefault(path, []).append((status, body, headers or {}))

    def submit(self, body):
        token = body.get("idempotency_token")
        if token and token in self.tokens:
            return self.tokens[token]
        self.next_id += 1
        rid = self.next_id
        seq_builder, outputs, cancel_builder = self.pending.pop(0)
        self.runs[rid] = {
            "seq": seq_builder(rid), "i": 0,
            "cancel_seq": cancel_builder(rid) if cancel_builder else None,
        }
        self.parent_ids.add(rid)
        for pos, out in outputs.items():
            self.outputs[rid * 10 + pos] = out
        if token:
            self.tokens[token] = rid
        self.created += 1
        return rid


def make_handler(inst):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _record(self):
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            req = {
                "method": self.command,
                "path": parsed.path,
                "params": {k: v[0] for k, v in parse_qs(parsed.query).items()},
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": json.loads(raw) if raw else None,
            }
            inst.requests.append(req)
            return req

        def _send(self, status, body=b"", headers=None):
            self.send_response(status)
            hdrs = dict(headers or {})
            hdrs.setdefault("Content-Type", "application/json")
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _fault(self, req):
            entry = None
            if req["path"] in inst.always_fail:
                entry = inst.always_fail[req["path"]]
            elif inst.fail_queue.get(req["path"]):
                entry = inst.fail_queue[req["path"]].pop(0)
            if entry is None:
                return False
            status, body, headers = entry
            self._send(status, body, headers)
            return True

        def _dispatch(self):
            req = self._record()
            if self._fault(req):
                return
            if req["headers"].get("authorization") != EXPECTED_AUTH:
                self._send(401, envelope("UNAUTHENTICATED",
                                         "Unable to authenticate the request"))
                return
            if req["method"] == "POST" and req["path"] == SUBMIT:
                rid = inst.submit(req["body"])
                self._send(200, json.dumps({"run_id": rid}).encode())
            elif req["method"] == "GET" and req["path"] == GET_RUN:
                rid = int(req["params"].get("run_id", "0"))
                run = inst.runs.get(rid)
                if run is None:
                    self._send(400, envelope("INVALID_PARAMETER_VALUE",
                                             f"Run {rid} does not exist."))
                    return
                doc = run["seq"][min(run["i"], len(run["seq"]) - 1)]
                run["i"] += 1
                self._send(200, json.dumps(doc).encode())
            elif req["method"] == "GET" and req["path"] == GET_OUTPUT:
                rid = int(req["params"].get("run_id", "0"))
                if rid in inst.parent_ids:
                    self._send(400, envelope(
                        "INVALID_PARAMETER_VALUE",
                        "A job run with multiple tasks does not have an output; "
                        "retrieve the output of each individual task run instead."))
                    return
                out = inst.outputs.get(rid)
                if out is None:
                    self._send(400, envelope("INVALID_PARAMETER_VALUE",
                                             f"Run {rid} does not exist."))
                    return
                self._send(200, json.dumps(out).encode())
            elif req["method"] == "POST" and req["path"] == CANCEL:
                inst.cancel_bodies.append(req["body"])
                rid = int(req["body"].get("run_id", 0))
                run = inst.runs.get(rid)
                if run and run["cancel_seq"]:
                    run["seq"] = run["cancel_seq"]
                    run["i"] = 0
                self._send(200, b"{}")
            else:
                self._send(404, envelope("ENDPOINT_NOT_FOUND",
                                         f"No API found for {req['method']} {req['path']}"))

        do_GET = _dispatch
        do_POST = _dispatch

    return Handler


def start(inst):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(inst))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def fresh():
    from dbxjobs.client import JobsClient

    inst = FakeWorkspace()
    srv, base = start(inst)
    sleeps = []
    client = JobsClient(base, TOKEN, sleep=sleeps.append,
                        max_retries=MAX_RETRIES, backoff_seconds=1.0)
    return inst, srv, client, sleeps


TASKS = [
    {"task_key": "extract",
     "notebook_task": {"notebook_path": "/Repos/ops/nightly/extract"}},
    {"task_key": "load", "depends_on": [{"task_key": "extract"}],
     "spark_python_task": {"python_file": "dbfs:/pipelines/load.py",
                           "parameters": ["--target", "silver.events"]}},
]


def success_seq(rid):
    running = [task_entry(rid, 0, "extract", "RUNNING"),
               task_entry(rid, 1, "load", "PENDING")]
    done = [task_entry(rid, 0, "extract", "TERMINATED", "SUCCESS"),
            task_entry(rid, 1, "load", "TERMINATED", "SUCCESS")]
    return [
        run_doc(rid, "PENDING", []),
        run_doc(rid, "RUNNING", running),
        run_doc(rid, "TERMINATED", done, code="SUCCESS", ttype="SUCCESS",
                message="", result="SUCCESS"),
    ]


def reqs(inst, method, path):
    return [r for r in inst.requests if r["method"] == method and r["path"] == path]


def test_submit_poll_and_collect_outputs():
    from dbxjobs.runner import OneTimeRunner

    inst, srv, client, sleeps = fresh()
    inst.script(success_seq, outputs={
        0: {"notebook_output": {"result": "rows=1284", "truncated": False}},
        1: {"logs": "load finished", "logs_truncated": False},
    })
    runner = OneTimeRunner(client, poll_interval=POLL_INTERVAL)
    report = runner.run(TASKS, run_name="nightly etl smoke")

    rid = 7001
    assert report.run_id == rid, f"run_id should be {rid}, got {report.run_id}"
    assert report.life_cycle_state == "TERMINATED", report.life_cycle_state
    assert report.result_state == "SUCCESS", report.result_state
    assert report.termination == {"code": "SUCCESS", "type": "SUCCESS", "message": ""}, \
        f"termination_details must be preserved verbatim: {report.termination}"
    assert report.canceled is False

    submits = reqs(inst, "POST", SUBMIT)
    assert len(submits) == 1, f"exactly one submit, saw {len(submits)}"
    body = submits[0]["body"]
    assert body["run_name"] == "nightly etl smoke"
    assert body["tasks"] == TASKS, \
        "the tasks array must be passed through to runs/submit verbatim"
    tok = body.get("idempotency_token")
    assert isinstance(tok, str) and 1 <= len(tok) <= 64, \
        "runner must generate an idempotency_token (<=64 chars) when none is given"
    assert submits[0]["headers"].get("content-type") == "application/json"
    assert submits[0]["headers"].get("accept") == "application/json", \
        "every call must send Accept: application/json"
    assert submits[0]["headers"].get("authorization") == EXPECTED_AUTH

    polls = reqs(inst, "GET", GET_RUN)
    assert len(polls) == 3, f"PENDING/RUNNING/TERMINATED needs exactly 3 polls, saw {len(polls)}"
    for p in polls:
        assert p["params"] == {"run_id": str(rid)}, \
            f"runs/get must be keyed by run_id query param, got {p['params']}"
        assert p["headers"].get("accept") == "application/json"
    assert sleeps == [POLL_INTERVAL, POLL_INTERVAL], \
        f"one injected poll_interval sleep between polls, got {sleeps}"

    outs = reqs(inst, "GET", GET_OUTPUT)
    assert len(outs) == 2, f"one get-output per task run, saw {len(outs)}"
    assert [o["params"]["run_id"] for o in outs] == [str(rid * 10), str(rid * 10 + 1)], \
        "get-output must target each TASK run_id in task order, never the parent"
    assert report.task_outputs["extract"]["notebook_output"]["result"] == "rows=1284"
    assert report.task_outputs["extract"]["notebook_output"]["truncated"] is False
    assert report.task_outputs["load"]["logs"] == "load finished"
    assert report.task_errors == {}, f"no task errors on success: {report.task_errors}"
    srv.shutdown()


def test_idempotent_resubmit_after_500():
    from dbxjobs.runner import OneTimeRunner

    inst, srv, client, sleeps = fresh()
    inst.script(success_seq, outputs={0: {"notebook_output": {"result": "", "truncated": False}},
                                      1: {"logs": "", "logs_truncated": False}})
    inst.fail_once(SUBMIT, 500, envelope("INTERNAL_ERROR", "Please try again."))
    report = OneTimeRunner(client, poll_interval=POLL_INTERVAL).run(
        TASKS, run_name="retry me", idempotency_token="etl-nightly-2026-07-17")

    submits = reqs(inst, "POST", SUBMIT)
    assert len(submits) == 2, "a 500 on submit must be retried exactly once here"
    assert submits[0]["body"].get("idempotency_token") == "etl-nightly-2026-07-17", \
        "a caller-supplied idempotency_token must be sent verbatim"
    assert submits[1]["body"].get("idempotency_token") == "etl-nightly-2026-07-17", \
        "the retry must reuse the SAME idempotency_token — that is what makes it safe"
    assert submits[0]["body"] == submits[1]["body"], "the retried submit body must be identical"
    assert inst.created == 1, "exactly one run may ever be launched per token"
    assert report.run_id == 7001 and report.result_state == "SUCCESS"
    assert sleeps[0] == 1.0, \
        f"transient 5xx retry must wait backoff_seconds via the injected sleep, got {sleeps[:1]}"
    srv.shutdown()


def test_task_failure_collects_errors_and_sibling_output():
    from dbxjobs.runner import OneTimeRunner

    def failed_seq(rid):
        done = [task_entry(rid, 0, "extract", "TERMINATED", "SUCCESS"),
                task_entry(rid, 1, "load", "TERMINATED", "FAILED")]
        return [
            run_doc(rid, "RUNNING", done),
            run_doc(rid, "TERMINATED", done, code="RUN_EXECUTION_ERROR",
                    ttype="CLIENT_ERROR",
                    message="Task load failed with message: division by zero",
                    result="FAILED"),
        ]

    inst, srv, client, _ = fresh()
    inst.script(failed_seq, outputs={
        0: {"notebook_output": {"result": "rows=17", "truncated": False}},
        1: {"error": "ZeroDivisionError: division by zero",
            "error_trace": "Traceback (most recent call last):\n  File \"load.py\", line 40"},
    })
    report = OneTimeRunner(client, poll_interval=POLL_INTERVAL).run(
        TASKS, run_name="nightly etl")

    assert report.result_state == "FAILED"
    assert report.life_cycle_state == "TERMINATED"
    assert report.termination["code"] == "RUN_EXECUTION_ERROR"
    assert report.termination["type"] == "CLIENT_ERROR"
    assert "division by zero" in report.termination["message"]
    assert report.task_errors["load"]["error"] == "ZeroDivisionError: division by zero"
    assert report.task_errors["load"]["error_trace"].startswith("Traceback"), \
        "error_trace must be preserved for the failed task"
    assert "extract" not in report.task_errors, \
        "a successful sibling task must not be reported as failed"
    assert report.task_outputs["extract"]["notebook_output"]["result"] == "rows=17", \
        "output of the successful sibling must still be collected"
    outs = reqs(inst, "GET", GET_OUTPUT)
    assert len(outs) == 2, "output must be fetched for every task, including the failed one"
    assert str(report.run_id) not in [o["params"]["run_id"] for o in outs], \
        "the multi-task parent run_id has no output — never call get-output with it"
    srv.shutdown()


def test_rate_limited_poll_retries_after_header():
    from dbxjobs.runner import OneTimeRunner

    inst, srv, client, sleeps = fresh()
    inst.script(success_seq, outputs={0: {"notebook_output": {"result": "", "truncated": False}},
                                      1: {"logs": "", "logs_truncated": False}})
    inst.fail_once(GET_RUN, 429, envelope("RESOURCE_EXHAUSTED", "Too many requests."),
                   {"Retry-After": "3"})
    report = OneTimeRunner(client, poll_interval=POLL_INTERVAL).run(TASKS, run_name="rl")

    assert report.result_state == "SUCCESS", "the rate-limited poll must still complete"
    assert sleeps == [3, POLL_INTERVAL, POLL_INTERVAL] or \
        sleeps == [3.0, POLL_INTERVAL, POLL_INTERVAL], \
        f"429 must sleep exactly the Retry-After seconds before retrying, got {sleeps}"
    assert len(reqs(inst, "GET", GET_RUN)) == 4, \
        "one 429 attempt plus three successful polls — no extras"
    srv.shutdown()


def test_rate_limit_exhaustion_raises_typed_error():
    from dbxjobs.errors import DbxApiError, RateLimitError

    inst, srv, client, sleeps = fresh()
    inst.always_fail[GET_RUN] = (429, envelope("RESOURCE_EXHAUSTED", "Too many requests."),
                                 {"Retry-After": "2"})
    raised = None
    try:
        client.get_run(1234)
    except RateLimitError as exc:
        raised = exc
    assert raised is not None, "persistent 429 must raise RateLimitError"
    assert isinstance(raised, DbxApiError), "RateLimitError must subclass DbxApiError"
    assert raised.status_code == 429
    assert raised.error_code == "RESOURCE_EXHAUSTED"
    assert raised.retry_after == 2, f"retry_after should carry the header value, got {raised.retry_after}"
    assert len(sleeps) == MAX_RETRIES, \
        f"sleep once per retry ({MAX_RETRIES}), then give up; slept {len(sleeps)}"
    assert len(reqs(inst, "GET", GET_RUN)) == MAX_RETRIES + 1, \
        "original attempt plus max_retries retries, then stop"
    srv.shutdown()


def test_caller_cancellation_cancels_run_and_reports_terminal_state():
    from dbxjobs.runner import OneTimeRunner

    def stuck_seq(rid):
        running = [task_entry(rid, 0, "extract", "RUNNING"),
                   task_entry(rid, 1, "load", "PENDING")]
        return [run_doc(rid, "RUNNING", running)]  # repeats forever

    def canceled_seq(rid):
        done = [task_entry(rid, 0, "extract", "TERMINATED", "CANCELED"),
                task_entry(rid, 1, "load", "TERMINATED", "CANCELED")]
        return [
            run_doc(rid, "TERMINATING", done),
            run_doc(rid, "TERMINATED", done, code="USER_CANCELED", ttype="SUCCESS",
                    message="Run canceled by user request.", result="CANCELED"),
        ]

    inst, srv, client, sleeps = fresh()
    inst.script(stuck_seq, cancel_seq_builder=canceled_seq)

    polls_seen = {"n": 0}

    def want_cancel():
        polls_seen["n"] += 1
        return polls_seen["n"] >= 3  # caller aborts on the third check

    report = OneTimeRunner(client, poll_interval=POLL_INTERVAL).run(
        TASKS, run_name="abort me", cancel=want_cancel)

    assert len(inst.cancel_bodies) == 1, \
        f"exactly one runs/cancel POST, saw {len(inst.cancel_bodies)}"
    assert inst.cancel_bodies[0] == {"run_id": 7001}, \
        f"cancel body must be exactly {{'run_id': 7001}}, got {inst.cancel_bodies[0]}"
    assert report.canceled is True, "the report must mark the run as canceled"
    assert report.result_state == "CANCELED"
    assert report.life_cycle_state == "TERMINATED", \
        "cancel is asynchronous — polling must continue to the terminal state"
    assert report.termination["code"] == "USER_CANCELED"
    assert report.termination["type"] == "SUCCESS"
    assert report.task_outputs == {} and report.task_errors == {}, \
        "canceled tasks have no output to collect"
    assert all(s == POLL_INTERVAL for s in sleeps), \
        f"only poll_interval sleeps are expected here, got {sleeps}"
    cancel_idx = next(i for i, r in enumerate(inst.requests) if r["path"] == CANCEL)
    later_gets = [r for r in inst.requests[cancel_idx + 1:] if r["path"] == GET_RUN]
    assert later_gets, "the runner must keep polling after cancel until TERMINATED"
    srv.shutdown()


def test_error_envelope_is_typed_and_redacted():
    from dbxjobs.errors import DbxApiError

    inst, srv, client, _ = fresh()
    inst.always_fail[GET_RUN] = (403, envelope(
        "PERMISSION_DENIED", "User does not have CAN_MANAGE_RUN on job run 42."), {})
    raised = None
    try:
        client.get_run(42)
    except DbxApiError as exc:
        raised = exc
    assert raised is not None, "a 403 envelope must raise DbxApiError"
    assert raised.status_code == 403
    assert raised.error_code == "PERMISSION_DENIED"
    assert raised.message == "User does not have CAN_MANAGE_RUN on job run 42."
    assert "PERMISSION_DENIED" in str(raised), "str(err) should surface the error_code"
    text = str(raised) + repr(raised)
    assert TOKEN not in text, "the bearer token leaked into the exception"
    assert EXPECTED_AUTH not in text, "the Authorization value leaked into the exception"
    srv.shutdown()


def test_protected_docs_fixtures():
    research = SOURCES["research"]
    assert research["required"] is True, "wave-8 seeds must record research provenance"
    assert len(research["official_sources"]) >= 2, "at least two official sources required"
    for src in research["official_sources"]:
        assert src["url"].startswith("https://"), f"non-https source {src['url']}"
        assert "databricks" in src["url"], "sources must be first-party Databricks pages"
        assert src.get("used_for"), "each source must say which facts it backed"
    assert len(SOURCES["verified_facts"]) >= 4, "contract facts must be summarized"
    ops = CONTRACT["operations"]
    assert ops["submit"]["path"] == "/api/2.2/jobs/runs/submit"
    assert ops["get_run"]["path"] == "/api/2.2/jobs/runs/get"
    assert ops["get_output"]["path"] == "/api/2.2/jobs/runs/get-output"
    assert ops["cancel"]["path"] == "/api/2.2/jobs/runs/cancel"
    assert CONTRACT["idempotency"]["max_chars"] == 64
    assert CONTRACT["rate_limit"]["retry_after_header"] == "Retry-After"
    assert "TERMINATED" in CONTRACT["run_states"]["status_state_enum"]


def main():
    tests = [
        test_protected_docs_fixtures,
        test_submit_poll_and_collect_outputs,
        test_idempotent_resubmit_after_500,
        test_task_failure_collects_errors_and_sibling_output,
        test_rate_limited_poll_retries_after_header,
        test_rate_limit_exhaustion_raises_typed_error,
        test_caller_cancellation_cancels_run_and_reports_terminal_state,
        test_error_envelope_is_typed_and_redacted,
    ]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"PASS  {len(tests)} test groups")


if __name__ == "__main__":
    main()
