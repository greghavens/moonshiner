"""Acceptance tests for the sfsql package.

Runs a loopback fake Snowflake SQL API v2 endpoint and drives sfsql against
it. No network beyond 127.0.0.1, no real credentials. The wire contract the
fake enforces is pinned in docs/contract.json (provenance in
docs/official_sources.json); both are protected fixtures.
"""

import json
import os
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "docs", "contract.json"), "r", encoding="utf-8") as fh:
    CONTRACT = json.load(fh)
with open(os.path.join(HERE, "docs", "official_sources.json"), "r", encoding="utf-8") as fh:
    SOURCES = json.load(fh)

TOKEN = "dummy-jwt-e5a91c44b0"  # dummy; must never leak into errors or logs
TOKEN_TYPE = CONTRACT["auth"]["token_type"]
USER_AGENT = CONTRACT["auth"]["request_headers"]["User-Agent"]
BASE_PATH = CONTRACT["base_path"]
SCHEDULE = CONTRACT["poll"]["sleep_schedule_seconds"]
MAX_POLLS = CONTRACT["poll"]["max_polls"]

H1 = "01b66701-0000-0001-0000-000000000001"
H2 = "01b66701-0000-0002-0000-000000000002"

RESULT_META = {
    "numRows": 2,
    "format": "jsonv2",
    "rowType": [
        {"name": "ID", "type": "FIXED", "length": 0, "precision": 38,
         "scale": 0, "nullable": False},
        {"name": "REGION", "type": "TEXT", "length": 16777216, "precision": 0,
         "scale": 0, "nullable": True},
        {"name": "AMOUNT", "type": "REAL", "length": 0, "precision": 0,
         "scale": 0, "nullable": True},
    ],
    "partitionInfo": [{"rowCount": 2, "uncompressedSize": 128}],
}
RESULT_DATA = [["1", "us-east", "3.5"], ["2", None, "12.25"]]

FAILURE = {
    "code": CONTRACT["responses"]["failure_422"]["fixture"]["code"],
    "sqlState": CONTRACT["responses"]["failure_422"]["fixture"]["sqlState"],
    "message": "SQL compilation error:\nsyntax error line 1 at position 7 "
               "unexpected 'FORM'.",
    "statementHandle": H2,
}


def status_url(handle, rid):
    return f"{BASE_PATH}/{handle}?requestId={rid}"


def result_body(handle):
    return {
        "code": "090001",
        "sqlState": "00000",
        "message": "Statement executed successfully.",
        "statementHandle": handle,
        "createdOn": 1752724800000,
        "statementStatusUrl": status_url(handle, "11111111-1111-1111-1111-111111111111"),
        "resultSetMetaData": RESULT_META,
        "data": RESULT_DATA,
    }


def query_status(handle, code, message):
    return {
        "code": code,
        "message": message,
        "statementHandle": handle,
        "statementStatusUrl": status_url(handle, "11111111-1111-1111-1111-111111111111"),
    }


class FakeSnowflake:
    """Scripted fake for the /api/v2/statements subset this seed pins.

    post_plan / get_plan hold one spec per expected request, consumed in
    order. A POST spec ("drop", ...) closes the connection before writing a
    status line (ambiguous outcome) while still recording the execution, so
    the requestId dedupe path can be exercised.
    """

    def __init__(self):
        self.requests = []       # dicts: method, path, raw, params, headers, body
        self.post_plan = []
        self.get_plan = []
        self.executions = {}     # requestId -> {"count": int, "spec": spec}
        self.drop_retries = False  # kill retry=true resubmissions too

    def record(self, handler, body):
        parsed = urlparse(handler.path)
        params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        req = {
            "method": handler.command,
            "path": parsed.path,
            "raw": handler.path,
            "params": params,
            "headers": {k.lower(): v for k, v in handler.headers.items()},
            "body": json.loads(body) if body else None,
        }
        self.requests.append(req)
        return req


def make_handler(fake):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _reply(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _drop(self):
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            req = fake.record(self, self.rfile.read(length))
            if urlparse(self.path).path != BASE_PATH:
                self._reply(404, {"message": "unknown endpoint"})
                return
            rid = req["params"].get("requestId", "")
            seen = fake.executions.get(rid)
            if seen and req["params"].get("retry") == "true":
                # documented resubmission: same requestId + retry=true is
                # answered from the original execution, never re-executed
                if fake.drop_retries:
                    self._drop()
                    return
                self._reply(200, result_body(seen["spec"][1]))
                return
            if not fake.post_plan:
                self._reply(500, {"message": "fake exhausted"})
                return
            spec = fake.post_plan.pop(0)
            kind = spec[0]
            if kind in ("drop", "200", "202", "408"):
                entry = fake.executions.setdefault(rid, {"count": 0, "spec": spec})
                entry["count"] += 1
                entry["spec"] = spec
            if kind == "drop":
                self._drop()
                return
            if kind == "200":
                self._reply(200, result_body(spec[1]))
            elif kind == "202":
                self._reply(202, query_status(spec[1], "333334",
                            "Asynchronous execution in progress."))
            elif kind == "408":
                self._reply(408, query_status(
                    spec[1], CONTRACT["responses"]["timeout_408"]["mock_code"],
                    "Statement reached its timeout of 5 second(s) and was canceled."))
            elif kind == "422":
                self._reply(422, spec[1])
            elif kind == "401":
                self._reply(401, {"message": "Authorization token has expired."})
            else:
                raise AssertionError(f"unknown post spec {spec!r}")

        def do_GET(self):
            req = fake.record(self, b"")
            if not urlparse(self.path).path.startswith(BASE_PATH + "/"):
                self._reply(404, {"message": "unknown endpoint"})
                return
            handle = urlparse(self.path).path.rsplit("/", 1)[-1]
            if not fake.get_plan:
                self._reply(500, {"message": "fake exhausted"})
                return
            spec = fake.get_plan.pop(0)
            kind = spec[0]
            if kind == "202":
                self._reply(202, query_status(handle, "333334",
                            "Asynchronous execution in progress."))
            elif kind == "200":
                self._reply(200, result_body(handle))
            elif kind == "422":
                self._reply(422, spec[1])
            else:
                raise AssertionError(f"unknown get spec {spec!r}")

    return Handler


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # deliberate connection drops are part of the script


def start():
    fake = FakeSnowflake()
    srv = QuietServer(("127.0.0.1", 0), make_handler(fake))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    return fake, srv, base


def uuid_seq(start=1):
    n = [start - 1]

    def next_id():
        n[0] += 1
        return str(uuid.UUID(int=n[0]))

    return next_id


def fresh(**runner_kw):
    from sfsql import SqlApiSession, StatementRunner

    fake, srv, base = start()
    session = SqlApiSession(
        base_url=base,
        token=TOKEN,
        token_type=TOKEN_TYPE,
        user_agent=USER_AGENT,
        request_ids=uuid_seq(),
    )
    sleeps = []
    runner = StatementRunner(session, sleeper=sleeps.append, **runner_kw)
    return fake, srv, runner, sleeps


def posts(fake):
    return [r for r in fake.requests if r["method"] == "POST"]


def gets(fake):
    return [r for r in fake.requests if r["method"] == "GET"]


def check_common_headers(req):
    h = req["headers"]
    assert h.get("authorization") == f"Bearer {TOKEN}", \
        "every request must carry Authorization: Bearer <token>"
    assert h.get("x-snowflake-authorization-token-type") == TOKEN_TYPE, \
        "the token type header must declare KEYPAIR_JWT"
    assert h.get("accept") == "application/json", "Accept must be application/json"
    assert h.get("user-agent") == USER_AGENT, \
        "the SQL API requires a real User-Agent; the default library UA is not ours"


def test_protected_docs_fixtures():
    research = SOURCES["research"]
    assert research["required"] is True, "wave-8 seeds must record research provenance"
    assert len(research["official_sources"]) >= 2, "at least two official sources required"
    for src in research["official_sources"]:
        assert src["url"].startswith("https://docs.snowflake.com/"), \
            "sources must be first-party Snowflake documentation pages"
        assert src.get("used_for"), "each source must say which facts it backed"
    assert len(SOURCES["verified_facts"]) >= 4
    assert CONTRACT["base_path"] == "/api/v2/statements"
    assert CONTRACT["responses"]["success_200"]["code"] == "090001"
    assert CONTRACT["responses"]["async_202"]["code"] == "333334"
    assert CONTRACT["auth"]["token_type_header"] == "X-Snowflake-Authorization-Token-Type"
    assert CONTRACT["poll"]["max_polls"] == len(CONTRACT["poll"]["sleep_schedule_seconds"])


def test_submit_request_shape():
    fake, srv, runner, _ = fresh()
    fake.post_plan.append(("200", H1))
    runner.run(
        "select id, region, amount from sales where region = ?",
        timeout=30, database="ANALYTICS", schema="PUBLIC",
        warehouse="WH_ETL", role="REPORTER", binds=["us-east"],
    )
    reqs = posts(fake)
    assert len(reqs) == 1, "one statement means exactly one POST"
    req = reqs[0]
    assert req["path"] == BASE_PATH, f"submit must POST {BASE_PATH}, got {req['path']}"
    check_common_headers(req)
    assert req["headers"].get("content-type") == "application/json"
    rid = req["params"].get("requestId", "")
    assert rid == str(uuid.UUID(int=1)), \
        "requestId must come from the injected id factory, one per statement"
    assert "retry" not in req["params"], "a first attempt must not claim retry=true"
    assert "async" not in req["params"], "this runner submits nominally-synchronous requests"
    assert req["body"] == {
        "statement": "select id, region, amount from sales where region = ?",
        "timeout": 30,
        "database": "ANALYTICS",
        "schema": "PUBLIC",
        "warehouse": "WH_ETL",
        "role": "REPORTER",
        "bindings": {"1": {"type": "TEXT", "value": "us-east"}},
    }, f"unexpected submit body {req['body']!r}"
    srv.shutdown()


def test_optional_fields_are_omitted():
    fake, srv, runner, _ = fresh()
    fake.post_plan.append(("200", H1))
    runner.run("select 1")
    body = posts(fake)[0]["body"]
    assert body == {"statement": "select 1"}, \
        "unset context fields must be absent from the body, not null: " + repr(body)
    srv.shutdown()


def test_bindings_encoding():
    fake, srv, runner, _ = fresh()
    fake.post_plan.append(("200", H1))
    runner.run("insert into audit values (?, ?, ?, ?)",
               binds=[42, "trail-7", 3.5, True])
    bindings = posts(fake)[0]["body"]["bindings"]
    assert bindings == {
        "1": {"type": "FIXED", "value": "42"},
        "2": {"type": "TEXT", "value": "trail-7"},
        "3": {"type": "REAL", "value": "3.5"},
        "4": {"type": "BOOLEAN", "value": "true"},
    }, f"documented positional string-encoded bindings required, got {bindings!r}"
    for slot in bindings.values():
        assert isinstance(slot["value"], str), \
            "the SQL API requires every binding value to be a string"
    srv.shutdown()


def test_synchronous_success():
    fake, srv, runner, sleeps = fresh()
    fake.post_plan.append(("200", H1))
    result = runner.run("select id, region, amount from sales")
    assert result.statement_handle == H1
    assert result.code == "090001"
    assert result.num_rows == 2
    assert [c.name for c in result.columns] == ["ID", "REGION", "AMOUNT"]
    assert [c.type for c in result.columns] == ["FIXED", "TEXT", "REAL"]
    assert [c.nullable for c in result.columns] == [False, True, True]
    assert result.rows == [["1", "us-east", "3.5"], ["2", None, "12.25"]], \
        "values stay string-encoded exactly as sent; SQL NULL becomes None"
    assert sleeps == [], "a 200 on submit must not sleep or poll"
    assert gets(fake) == [], "a 200 on submit must not hit the status endpoint"
    srv.shutdown()


def test_async_202_polls_status_url():
    fake, srv, runner, sleeps = fresh()
    fake.post_plan.append(("202", H1))
    fake.get_plan.extend([("202",), ("202",), ("200",)])
    result = runner.run("call analytics.refresh_rollups()")
    assert result.statement_handle == H1
    assert result.rows == RESULT_DATA
    polls = gets(fake)
    assert len(polls) == 3, f"two in-progress polls plus the final one, saw {len(polls)}"
    expected = status_url(H1, "11111111-1111-1111-1111-111111111111")
    for p in polls:
        assert p["raw"] == expected, \
            "polling must GET exactly the statementStatusUrl the server returned"
        check_common_headers(p)
        assert "content-type" not in p["headers"], "GET polls carry no Content-Type"
    assert sleeps == SCHEDULE[:3], \
        f"sleeper must be called with the pinned schedule, got {sleeps!r}"
    assert len(posts(fake)) == 1, "polling must never resubmit the statement"
    srv.shutdown()


def test_timeout_408_preserves_handle():
    from sfsql import StatementTimedOut

    fake, srv, runner, sleeps = fresh()
    fake.post_plan.append(("408", H2))
    try:
        runner.run("select * from telemetry.events", timeout=5)
        raise AssertionError("a 408 submission must raise StatementTimedOut")
    except StatementTimedOut as e:
        assert e.statement_handle == H2, \
            "the 408 QueryStatus body still names the statement handle; keep it"
        assert e.status_url == status_url(H2, "11111111-1111-1111-1111-111111111111")
    assert len(fake.requests) == 1, "a 408 is terminal here: no polls, no resubmit"
    assert sleeps == []
    srv.shutdown()


def test_sql_failure_is_structured():
    from sfsql import StatementFailed

    fake, srv, runner, _ = fresh()
    fake.post_plan.append(("202", H2))
    fake.get_plan.append(("422", FAILURE))
    try:
        runner.run("select * form telemetry.events")
        raise AssertionError("a 422 QueryFailureStatus must raise StatementFailed")
    except StatementFailed as e:
        assert e.code == FAILURE["code"]
        assert e.sql_state == FAILURE["sqlState"]
        assert e.message == FAILURE["message"]
        assert e.statement_handle == H2
        text = str(e)
        assert FAILURE["code"] in text and FAILURE["sqlState"] in text, \
            "the error text must carry the Snowflake code and sqlState"
        assert TOKEN not in text and TOKEN not in repr(e), \
            "the bearer token must never appear in error text"
    srv.shutdown()


def test_direct_422_on_submit():
    from sfsql import StatementFailed

    fake, srv, runner, _ = fresh()
    fake.post_plan.append(("422", FAILURE))
    try:
        runner.run("select * form telemetry.events")
        raise AssertionError("StatementFailed expected")
    except StatementFailed as e:
        assert (e.code, e.sql_state) == (FAILURE["code"], FAILURE["sqlState"])
    assert gets(fake) == [], "an immediate failure is terminal; nothing to poll"
    srv.shutdown()


def test_ambiguous_drop_resubmits_same_request_id():
    fake, srv, runner, _ = fresh()
    fake.post_plan.append(("drop", H1))
    result = runner.run("insert into ledger values (7, 'credit')")
    assert result.statement_handle == H1
    reqs = posts(fake)
    assert len(reqs) == 2, "one ambiguous drop means exactly one resubmission"
    first, second = reqs
    assert "retry" not in first["params"]
    assert second["params"].get("retry") == "true", \
        "the resubmission must declare retry=true"
    assert second["params"].get("requestId") == first["params"].get("requestId"), \
        "the resubmission must reuse the SAME requestId so the server can dedupe"
    assert second["body"] == first["body"]
    rid = first["params"]["requestId"]
    assert fake.executions[rid]["count"] == 1, \
        "the server must have executed the statement exactly once"
    srv.shutdown()


def test_drop_happens_only_once():
    from sfsql import SqlApiError

    fake, srv, runner, _ = fresh()
    fake.post_plan.append(("drop", H1))
    fake.drop_retries = True  # the resubmission dies too -> give up
    try:
        runner.run("insert into ledger values (8, 'debit')")
        raise AssertionError("two dropped connections must surface an error")
    except SqlApiError:
        pass
    assert len(posts(fake)) == 2, \
        "exactly one resubmission is allowed after an ambiguous failure"
    srv.shutdown()


def test_poll_budget_is_bounded():
    from sfsql import SqlApiError

    fake, srv, runner, sleeps = fresh()
    fake.post_plan.append(("202", H1))
    fake.get_plan.extend([("202",)] * (MAX_POLLS + 3))
    try:
        runner.run("call analytics.rebuild_everything()")
        raise AssertionError("an endless 202 stream must exhaust the poll budget")
    except SqlApiError as e:
        assert not isinstance(e, AssertionError)
        assert TOKEN not in str(e)
    assert len(gets(fake)) == MAX_POLLS, \
        f"exactly {MAX_POLLS} polls allowed, saw {len(gets(fake))}"
    assert sleeps == SCHEDULE, f"pinned sleep schedule violated: {sleeps!r}"
    srv.shutdown()


def test_401_never_leaks_token():
    from sfsql import SqlApiError

    fake, srv, runner, _ = fresh()
    fake.post_plan.append(("401",))
    try:
        runner.run("select 1")
        raise AssertionError("a 401 must raise SqlApiError")
    except SqlApiError as e:
        assert getattr(e, "status", None) == 401, "the HTTP status must be preserved"
        assert TOKEN not in str(e) and TOKEN not in repr(e), \
            "the bearer token must never leak into the exception"
    srv.shutdown()


def main():
    tests = [
        test_protected_docs_fixtures,
        test_submit_request_shape,
        test_optional_fields_are_omitted,
        test_bindings_encoding,
        test_synchronous_success,
        test_async_202_polls_status_url,
        test_timeout_408_preserves_handle,
        test_sql_failure_is_structured,
        test_direct_422_on_submit,
        test_ambiguous_drop_resubmits_same_request_id,
        test_drop_happens_only_once,
        test_poll_budget_is_bounded,
        test_401_never_leaks_token,
    ]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"PASS  {len(tests)} test groups")


if __name__ == "__main__":
    main()
