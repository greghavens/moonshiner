"""Acceptance tests for the JQL search exporter.

Everything runs against a local http.server mock speaking the Jira Cloud
enhanced-search wire contract pinned in docs/contract.json — no real site,
no real credentials, no network beyond loopback.
"""

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from jiraexport.client import JiraSearchClient, JiraApiError
from jiraexport.exporter import export_jsonl

EMAIL = "reporting@example.com"
TOKEN = "dummy-jira-api-token-5860"
BASIC = "Basic " + base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()

EXPORT_PATH = "issues_export.jsonl"


class MockJira:
    """Loopback Jira that records every request and serves scripted pages."""

    def __init__(self, serve):
        self.requests = []
        self.serve = serve
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self._handle()

            def do_GET(self):
                self._handle()

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                captured = {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": json.loads(raw) if raw else None,
                }
                n = len(mock.requests)
                mock.requests.append(captured)
                status, body = mock.serve(n, captured)
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json;charset=UTF-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# Field payloads chosen to prove Jira's typed values survive untouched.
ISSUE_A = {
    "id": "20101",
    "key": "SUP-101",
    "self": "https://example.atlassian.net/rest/api/3/issue/20101",
    "fields": {
        "summary": "Café checkout broken — naïve retry loops",
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "labels": ["checkout", "sev1"],
        "customfield_10024": 5,
        "customfield_10031": 3.5,
        "duedate": None,
        "resolution": None,
    },
}
ISSUE_B = {
    "id": "20102",
    "key": "SUP-102",
    "self": "https://example.atlassian.net/rest/api/3/issue/20102",
    "fields": {
        "summary": "Refund totals drift",
        "status": {"name": "To Do", "statusCategory": {"key": "new"}},
        "labels": [],
        "customfield_10024": 0,
        "customfield_10031": None,
        "duedate": "2026-08-03",
        "resolution": None,
    },
}
ISSUE_C = {
    "id": "20103",
    "key": "SUP-103",
    "self": "https://example.atlassian.net/rest/api/3/issue/20103",
    "fields": {
        "summary": "Weekly digest empty",
        "status": {"name": "Done", "statusCategory": {"key": "done"}},
        "labels": ["digest"],
        "customfield_10024": 13,
        "customfield_10031": 0.25,
        "duedate": "2026-07-20",
        "resolution": {"name": "Fixed"},
    },
}

FIELDS = [
    "summary",
    "status",
    "labels",
    "customfield_10024",
    "customfield_10031",
    "duedate",
    "resolution",
]

JQL = "project = SUP AND updated >= -14d ORDER BY created ASC"


def paged_serve(n, req):
    if req["path"] != "/rest/api/3/search/jql":
        return 410, {
            "errorMessages": [
                "Gone. This endpoint was removed; use /rest/api/3/search/jql."
            ],
            "errors": {},
        }
    token = (req["body"] or {}).get("nextPageToken")
    if token is None:
        return 200, {"issues": [ISSUE_A], "isLast": False, "nextPageToken": "PT-1"}
    if token == "PT-1":
        # A legal intermediate page: zero issues but the search is not done.
        return 200, {"issues": [], "isLast": False, "nextPageToken": "PT-2"}
    if token == "PT-2":
        return 200, {"issues": [ISSUE_B, ISSUE_C], "isLast": True}
    return 400, {"errorMessages": ["The provided next page token is invalid or expired."], "errors": {}}


def test_search_all_follows_next_page_tokens():
    mock = MockJira(paged_serve)
    try:
        client = JiraSearchClient(mock.base, EMAIL, TOKEN)
        issues = list(client.search_all(JQL, fields=FIELDS, page_size=2))

        assert len(mock.requests) == 3, mock.requests
        for req in mock.requests:
            assert req["method"] == "POST", req
            assert req["path"] == "/rest/api/3/search/jql", req
            assert req["headers"]["authorization"] == BASIC
            assert req["headers"]["content-type"].startswith("application/json")
            body = req["body"]
            assert body["jql"] == JQL
            assert body["maxResults"] == 2
            assert body["fields"] == FIELDS
            assert "startAt" not in body, "startAt is the removed legacy pagination"

        first, second, third = (r["body"] for r in mock.requests)
        assert "nextPageToken" not in first, "the first page must not send a token"
        assert second["nextPageToken"] == "PT-1"
        assert third["nextPageToken"] == "PT-2"

        assert [i["key"] for i in issues] == ["SUP-101", "SUP-102", "SUP-103"]
        assert issues[0]["id"] == "20101"

        # Typed field values must arrive unchanged.
        fa = issues[0]["fields"]
        assert fa["customfield_10024"] == 5 and isinstance(fa["customfield_10024"], int)
        assert fa["customfield_10031"] == 3.5 and isinstance(fa["customfield_10031"], float)
        assert fa["duedate"] is None
        assert fa["status"]["statusCategory"]["key"] == "indeterminate"
        assert fa["summary"] == "Café checkout broken — naïve retry loops"
        fb = issues[1]["fields"]
        assert fb["customfield_10024"] == 0 and fb["customfield_10024"] is not False
        assert fb["labels"] == []
        assert fb["customfield_10031"] is None
        fc = issues[2]["fields"]
        assert fc["resolution"] == {"name": "Fixed"}
    finally:
        mock.close()


def test_single_page_and_expand_wiring():
    def serve(n, req):
        return 200, {"issues": [ISSUE_C], "isLast": True}

    mock = MockJira(serve)
    try:
        client = JiraSearchClient(mock.base, EMAIL, TOKEN)
        issues = list(client.search_all("assignee = currentUser()", fields=["summary"], expand="names"))

        assert len(mock.requests) == 1
        body = mock.requests[0]["body"]
        assert body["maxResults"] == 50, "the documented default page size is 50"
        assert body["fields"] == ["summary"]
        assert body["expand"] == "names", "expand travels as a comma-delimited string"
        assert "nextPageToken" not in body
        assert [i["key"] for i in issues] == ["SUP-103"]
    finally:
        mock.close()


def test_expand_omitted_when_not_requested():
    def serve(n, req):
        return 200, {"issues": [], "isLast": True}

    mock = MockJira(serve)
    try:
        client = JiraSearchClient(mock.base, EMAIL, TOKEN)
        issues = list(client.search_all("project = SUP", fields=["summary"]))
        assert issues == []
        body = mock.requests[0]["body"]
        assert "expand" not in body, "unused options must be omitted from the request"
    finally:
        mock.close()


def test_jql_errors_surface_the_error_collection():
    def serve(n, req):
        return 400, {
            "errorMessages": [
                "Error in the JQL Query: The character '}' is a reserved JQL character."
            ],
            "errors": {},
        }

    mock = MockJira(serve)
    try:
        client = JiraSearchClient(mock.base, EMAIL, TOKEN)
        try:
            list(client.search_all("project = SUP AND boom = }", fields=["summary"]))
            raise AssertionError("a 400 search must raise JiraApiError")
        except JiraApiError as err:
            assert err.status == 400
            assert "reserved JQL character" in str(err)
            assert err.error_messages == [
                "Error in the JQL Query: The character '}' is a reserved JQL character."
            ]
            assert TOKEN not in str(err), "error text leaks the API token"
            assert EMAIL not in str(err), "error text leaks the account email"
            assert BASIC.split()[1] not in str(err), "error text leaks the auth blob"
    finally:
        mock.close()


def test_export_jsonl_round_trips_typed_values():
    mock = MockJira(paged_serve)
    try:
        client = JiraSearchClient(mock.base, EMAIL, TOKEN)
        count = export_jsonl(client, JQL, FIELDS, EXPORT_PATH, page_size=2)
        assert count == 3

        with open(EXPORT_PATH, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh.read().splitlines() if line]

        assert [row["key"] for row in lines] == ["SUP-101", "SUP-102", "SUP-103"]
        for row in lines:
            assert set(row) == {"id", "key", "fields"}, row

        fa = lines[0]["fields"]
        assert fa["customfield_10024"] == 5 and isinstance(fa["customfield_10024"], int)
        assert fa["customfield_10031"] == 3.5 and isinstance(fa["customfield_10031"], float)
        assert fa["duedate"] is None
        assert fa["summary"] == "Café checkout broken — naïve retry loops"
        assert lines[1]["fields"]["duedate"] == "2026-08-03"
        assert lines[2]["fields"]["resolution"] == {"name": "Fixed"}
    finally:
        mock.close()


def main():
    tests = [
        test_search_all_follows_next_page_tokens,
        test_single_page_and_expand_wiring,
        test_expand_omitted_when_not_requested,
        test_jql_errors_surface_the_error_collection,
        test_export_jsonl_round_trips_typed_values,
    ]
    for fn in tests:
        if os.path.exists(EXPORT_PATH):
            os.remove(EXPORT_PATH)
        fn()
        print("ok  %s" % fn.__name__)
    print("all %d tests passed" % len(tests))


if __name__ == "__main__":
    main()
