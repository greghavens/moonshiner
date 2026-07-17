"""Acceptance tests for the Actions failed-job remediation feature.

Runs a loopback HTTP mock speaking the GitHub Actions REST wire contract
pinned in docs/contract.json. No real GitHub, no real credentials, no sleeps.
Protected — do not modify. Run: python3 test_actions_rerun.py
"""

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "ghp_dummyActionsMonitor4407"
OWNER = "machine-shop"
REPO = "paint-batch-api"
REPO_ID = 812345
PROBE = "k9d3"  # marker only present in server-issued Link URLs

ACCEPT = "application/vnd.github+json"
API_VERSION = "2026-03-10"


def run_obj(run_id, status, conclusion, name="nightly-regression"):
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": 1,
        "head_branch": "main",
        "event": "schedule",
    }


class MockState:
    def __init__(self):
        self.requests = []  # (method, path, query dict, headers, body bytes)
        self.polls = {}  # run_id -> list of (status, conclusion); last repeats
        self.rerun_status = 201
        self.lock = threading.Lock()

    def record(self, handler, body):
        parsed = urllib.parse.urlsplit(handler.path)
        query = urllib.parse.parse_qs(parsed.query)
        with self.lock:
            self.requests.append({
                "method": handler.command,
                "path": parsed.path,
                "raw_query": parsed.query,
                "query": {k: v[0] for k, v in query.items()},
                "headers": {k.lower(): v for k, v in handler.headers.items()},
                "body": body,
            })
        return parsed, query

    def next_poll(self, run_id):
        with self.lock:
            states = self.polls[run_id]
            state = states[0]
            if len(states) > 1:
                states.pop(0)
            return state

    def reruns_for(self, run_id):
        suffix = f"/actions/runs/{run_id}/rerun-failed-jobs"
        with self.lock:
            return [r for r in self.requests
                    if r["method"] == "POST" and r["path"].endswith(suffix)]

    def list_requests(self):
        with self.lock:
            return [r for r in self.requests
                    if r["method"] == "GET" and r["path"].endswith("/actions/runs")]


def make_handler(state, base_url_box):
    runs_path = f"/repos/{OWNER}/{REPO}/actions/runs"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, status, payload=None, headers=None):
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            if payload is None:
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            raw = json.dumps(payload).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            parsed, query = state.record(self, b"")
            base = base_url_box[0]
            if parsed.path == runs_path:
                next_url = (f"{base}/repositories/{REPO_ID}/actions/runs"
                            f"?status=failure&per_page=100&page=2&probe={PROBE}")
                link = f'<{next_url}>; rel="next", <{next_url}>; rel="last"'
                self._send(200, {
                    "total_count": 3,
                    "workflow_runs": [
                        run_obj(901, "completed", "failure"),
                        run_obj(902, "completed", "cancelled", name="deploy"),
                    ],
                }, headers={"Link": link})
            elif parsed.path == f"/repositories/{REPO_ID}/actions/runs":
                if query.get("probe", [None])[0] != PROBE:
                    self._send(400, {"message":
                                     "second page requested without the "
                                     "server-issued Link URL"})
                    return
                self._send(200, {
                    "total_count": 3,
                    "workflow_runs": [
                        run_obj(903, "completed", "failure", name="perf-suite"),
                    ],
                })
            elif parsed.path == f"{runs_path}/999":
                self._send(404, {"message": "Not Found",
                                 "documentation_url":
                                 "https://docs.github.com/rest"})
            elif parsed.path == f"{runs_path}/998":
                self._send(403, {"message":
                                 "API rate limit exceeded for installation "
                                 "ID 9581."})
            elif parsed.path.startswith(f"{runs_path}/"):
                run_id = int(parsed.path.rsplit("/", 1)[1])
                status, conclusion = state.next_poll(run_id)
                self._send(200, run_obj(run_id, status, conclusion))
            else:
                self._send(404, {"message": "Not Found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            parsed, _query = state.record(self, body)
            if parsed.path.endswith("/rerun-failed-jobs"):
                # Current docs: 201 Created with an empty body.
                self._send(state.rerun_status,
                           None if state.rerun_status == 201
                           else {"message": "Unable to re-run this workflow "
                                            "run because it was created over "
                                            "a month ago"})
            else:
                self._send(404, {"message": "Not Found"})

    return Handler


def start_server(state):
    box = [None]
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, box))
    box[0] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, box[0]


def fresh(monitor_kwargs=None):
    from ghactions.client import GitHubClient
    from ghactions.rerun import RerunMonitor

    state = MockState()
    state.polls = {
        901: [("queued", None), ("in_progress", None), ("completed", "success")],
        903: [("in_progress", None), ("completed", "failure")],
    }
    server, base = start_server(state)
    client = GitHubClient(base, TOKEN)
    paces = []

    def pace(attempt):
        paces.append(attempt)

    monitor = RerunMonitor(client, pace=pace, **(monitor_kwargs or {}))
    return state, server, client, monitor, paces


def check_common_headers(req):
    assert req["headers"].get("accept") == ACCEPT, \
        f"Accept header wrong: {req['headers'].get('accept')!r}"
    assert req["headers"].get("x-github-api-version") == API_VERSION, \
        f"X-GitHub-Api-Version must be the current dated version, got " \
        f"{req['headers'].get('x-github-api-version')!r}"
    assert req["headers"].get("authorization") == f"Bearer {TOKEN}", \
        f"Authorization wrong: {req['headers'].get('authorization')!r}"
    ua = req["headers"].get("user-agent", "")
    assert ua and not ua.startswith("Python-urllib"), \
        f"a real User-Agent is required, got {ua!r}"


def test_existing_single_page_listing_still_works():
    from ghactions.client import GitHubClient

    state = MockState()
    server, base = start_server(state)
    try:
        client = GitHubClient(base, TOKEN)
        page = client.list_workflow_runs(OWNER, REPO, status="failure",
                                         per_page=2)
        assert page["total_count"] == 3
        assert [r["id"] for r in page["workflow_runs"]] == [901, 902]
        req = state.requests[0]
        assert req["path"] == f"/repos/{OWNER}/{REPO}/actions/runs"
        assert req["query"]["status"] == "failure"
        assert req["query"]["per_page"] == "2"
        check_common_headers(req)
    finally:
        server.shutdown()


def test_existing_error_decoding_and_redaction():
    from ghactions.client import GitHubApiError, GitHubClient

    state = MockState()
    server, base = start_server(state)
    try:
        client = GitHubClient(base, TOKEN)
        try:
            client.get_workflow_run(OWNER, REPO, 999)
            raise AssertionError("404 must raise GitHubApiError")
        except GitHubApiError as exc:
            assert exc.status == 404
            assert "Not Found" in str(exc)
            assert TOKEN not in str(exc), "token leaked into error text"

        try:
            client.get_workflow_run(OWNER, REPO, 998)
            raise AssertionError("403 must raise GitHubApiError")
        except GitHubApiError as exc:
            assert exc.status == 403
            assert "rate limit" in str(exc)
            assert TOKEN not in str(exc), "token leaked into error text"
    finally:
        server.shutdown()


def test_iter_workflow_runs_follows_link_relations():
    from ghactions.client import GitHubClient

    state = MockState()
    server, base = start_server(state)
    try:
        client = GitHubClient(base, TOKEN)
        runs = list(client.iter_workflow_runs(OWNER, REPO, status="failure",
                                              per_page=100))
        assert [r["id"] for r in runs] == [901, 902, 903], \
            f"runs across pages wrong: {[r['id'] for r in runs]}"

        lists = state.list_requests()
        assert len(lists) == 2, f"expected 2 page requests, got {len(lists)}"
        first, second = lists
        assert first["path"] == f"/repos/{OWNER}/{REPO}/actions/runs"
        assert first["query"]["status"] == "failure"
        assert first["query"]["per_page"] == "100"
        # The Link rel="next" URL must be followed verbatim — GitHub may hand
        # back a canonical /repositories/{id}/ URL that cannot be rebuilt.
        assert second["path"] == f"/repositories/{REPO_ID}/actions/runs"
        assert second["query"].get("probe") == PROBE, \
            "rel=\"next\" URL was rebuilt instead of followed verbatim"
        check_common_headers(second)
    finally:
        server.shutdown()


def test_rerun_failed_jobs_handles_created_with_empty_body():
    state, server, client, _monitor, _paces = fresh()
    try:
        result = client.rerun_failed_jobs(OWNER, REPO, 901)
        assert result is None, "201-with-empty-body success returns None"
        posts = state.reruns_for(901)
        assert len(posts) == 1
        assert posts[0]["path"] == \
            f"/repos/{OWNER}/{REPO}/actions/runs/901/rerun-failed-jobs"
        assert posts[0]["body"] in (b"", b"{}"), \
            f"default rerun body should be empty, got {posts[0]['body']!r}"
        check_common_headers(posts[0])
    finally:
        server.shutdown()


def test_rerun_failed_jobs_debug_flag_in_body():
    state, server, client, _monitor, _paces = fresh()
    try:
        client.rerun_failed_jobs(OWNER, REPO, 901, enable_debug_logging=True)
        posts = state.reruns_for(901)
        assert json.loads(posts[0]["body"].decode("utf-8")) == \
            {"enable_debug_logging": True}
        assert "json" in posts[0]["headers"].get("content-type", ""), \
            "JSON body needs a JSON content type"
    finally:
        server.shutdown()


def test_rerun_failure_surfaces_github_message():
    from ghactions.client import GitHubApiError

    state, server, client, _monitor, _paces = fresh()
    state.rerun_status = 403
    try:
        try:
            client.rerun_failed_jobs(OWNER, REPO, 901)
            raise AssertionError("403 must raise GitHubApiError")
        except GitHubApiError as exc:
            assert exc.status == 403
            assert "over a month ago" in str(exc)
            assert TOKEN not in str(exc)
    finally:
        server.shutdown()


def test_wait_for_conclusion_polls_to_terminal():
    state, server, _client, monitor, paces = fresh()
    try:
        conclusion = monitor.wait_for_conclusion(OWNER, REPO, 901)
        assert conclusion == "success", conclusion
        polls = [r for r in state.requests
                 if r["method"] == "GET" and r["path"].endswith("/runs/901")]
        assert len(polls) == 3, \
            f"expected 3 polls (queued, in_progress, completed), got {len(polls)}"
        assert paces == [1, 2], \
            f"pace is called between consecutive polls only, got {paces}"
    finally:
        server.shutdown()


def test_wait_for_conclusion_gives_up_after_max_polls():
    from ghactions.rerun import RerunTimeout

    state, server, _client, monitor, paces = fresh({"max_polls": 4})
    state.polls[907] = [("in_progress", None)]  # never terminal
    try:
        try:
            monitor.wait_for_conclusion(OWNER, REPO, 907)
            raise AssertionError("non-terminal run must raise RerunTimeout")
        except RerunTimeout as exc:
            assert "907" in str(exc)
        polls = [r for r in state.requests
                 if r["method"] == "GET" and r["path"].endswith("/runs/907")]
        assert len(polls) == 4, f"max_polls=4 means exactly 4 GETs, got {len(polls)}"
    finally:
        server.shutdown()


def test_remediate_reruns_only_failed_runs_and_waits():
    state, server, _client, monitor, _paces = fresh()
    try:
        report = monitor.remediate(OWNER, REPO)
        assert report["results"] == {901: "success", 903: "failure"}, report
        assert report["skipped"] == [902], \
            "non-failure conclusions must be skipped even if the server " \
            "returns them in a status=failure listing"

        assert len(state.reruns_for(901)) == 1
        assert len(state.reruns_for(903)) == 1
        assert len(state.reruns_for(902)) == 0, \
            "cancelled run 902 must not be rerun"

        lists = state.list_requests()
        assert lists[0]["query"].get("status") == "failure", \
            "remediate lists runs with the status=failure filter"
        assert lists[0]["query"].get("per_page") == "100"

        # Rerun must come before the polls that observe it completing.
        order = [(r["method"], r["path"]) for r in state.requests]
        rerun_at = order.index(
            ("POST", f"/repos/{OWNER}/{REPO}/actions/runs/901/rerun-failed-jobs"))
        first_poll = order.index(
            ("GET", f"/repos/{OWNER}/{REPO}/actions/runs/901"))
        assert rerun_at < first_poll, \
            "the rerun POST must precede conclusion polling"
    finally:
        server.shutdown()


def main():
    tests = [
        test_existing_single_page_listing_still_works,
        test_existing_error_decoding_and_redaction,
        test_iter_workflow_runs_follows_link_relations,
        test_rerun_failed_jobs_handles_created_with_empty_body,
        test_rerun_failed_jobs_debug_flag_in_body,
        test_rerun_failure_surfaces_github_message,
        test_wait_for_conclusion_polls_to_terminal,
        test_wait_for_conclusion_gives_up_after_max_polls,
        test_remediate_reruns_only_failed_runs_and_waits,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
