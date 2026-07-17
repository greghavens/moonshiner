"""End-to-end tests for the workboard app.

Drives the WSGI callable directly (no sockets). Run: python3 test_workboard.py
"""
import io
import json

from app import create_app


def call(app, method, path, body=None):
    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_LENGTH": str(len(raw)),
        "CONTENT_TYPE": "application/json",
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(raw),
        "wsgi.errors": io.StringIO(),
    }
    captured = {}

    def start_response(status, headers):
        captured["status"] = int(status.split()[0])
        captured["headers"] = dict(headers)

    chunks = app(environ, start_response)
    text = b"".join(chunks).decode("utf-8")
    data = None
    if captured["headers"].get("Content-Type", "").startswith("application/json"):
        data = json.loads(text)
    return captured["status"], text, data


def test_project_and_item_crud():
    app = create_app()
    status, _, project = call(app, "POST", "/api/projects", {"name": "Apollo"})
    assert status == 201, status
    assert project["id"] == 1 and project["name"] == "Apollo", project
    assert project["archived"] is False, project

    status, _, got = call(app, "GET", "/api/projects/1")
    assert status == 200 and got["name"] == "Apollo", (status, got)

    status, _, item = call(app, "POST", "/api/projects/1/items",
                           {"title": "Design the intake flow"})
    assert status == 201 and item["status"] == "open", (status, item)

    status, _, closed = call(app, "POST", "/api/items/%d/close" % item["id"])
    assert status == 200 and closed["status"] == "done", (status, closed)

    status, _, listing = call(app, "GET", "/api/projects/1/items")
    assert status == 200 and len(listing["items"]) == 1, (status, listing)
    assert listing["items"][0]["status"] == "done", listing

    # validation and error mapping
    status, _, err = call(app, "POST", "/api/projects", {"name": "   "})
    assert status == 400, "blank project names must be rejected, got %s" % status
    status, _, err = call(app, "GET", "/api/projects/999")
    assert status == 404, "unknown project ids must 404, got %s" % status
    status, _, err = call(app, "GET", "/no/such/page")
    assert status == 404, status
    status, _, err = call(app, "DELETE", "/api/projects/1")
    assert status == 405, "unrouted methods on a known path must 405, got %s" % status


def test_dashboard_counts_follow_item_writes():
    app = create_app()
    call(app, "POST", "/api/projects", {"name": "Perseus"})
    call(app, "POST", "/api/projects/1/items", {"title": "Wire the relay"})
    call(app, "POST", "/api/projects/1/items", {"title": "Label the crates"})

    status, page, _ = call(app, "GET", "/")
    assert status == 200 and "Perseus" in page, "dashboard must list the project"
    assert "2 open" in page and "0 done" in page, page

    _, _, items = call(app, "GET", "/api/projects/1/items")
    call(app, "POST", "/api/items/%d/close" % items["items"][0]["id"])

    status, page, _ = call(app, "GET", "/")
    assert "1 open" in page and "1 done" in page, \
        "dashboard counts must reflect a just-closed item; page said:\n" + page


def test_route_table_lists_every_declared_route():
    app = create_app()
    status, _, dump = call(app, "GET", "/api/routes")
    assert status == 200, status
    routes = dump["routes"]
    assert "GET / -> dashboard" in routes, routes
    assert "POST /api/projects/<project_id>/archive -> archive_project" in routes, \
        "the archive route must appear in the route dump; got: %r" % routes


def test_rename_shows_up_everywhere():
    app = create_app()
    call(app, "POST", "/api/projects", {"name": "Apollo"})
    call(app, "POST", "/api/projects/1/items", {"title": "Stack the pallets"})

    # both pages have been viewed at least once before the rename
    status, page, _ = call(app, "GET", "/")
    assert status == 200 and "Apollo" in page, page
    status, page, _ = call(app, "GET", "/projects/1")
    assert status == 200 and "Apollo" in page, page

    status, _, renamed = call(app, "PATCH", "/api/projects/1",
                              {"name": "Artemis"})
    assert status == 200 and renamed["name"] == "Artemis", (status, renamed)

    status, _, got = call(app, "GET", "/api/projects/1")
    assert got["name"] == "Artemis", "API must report the new name: %r" % got

    status, page, _ = call(app, "GET", "/")
    assert "Artemis" in page, \
        "dashboard must show the new project name after a rename"
    assert "Apollo" not in page, \
        "dashboard must stop showing the old project name after a rename"

    status, page, _ = call(app, "GET", "/projects/1")
    assert "Artemis" in page and "Apollo" not in page, \
        "project page must show the new project name after a rename"


def test_archive_endpoint_is_reachable():
    app = create_app()
    call(app, "POST", "/api/projects", {"name": "Helios"})

    status, _, archived = call(app, "POST", "/api/projects/1/archive",
                               {"confirm": True})
    assert status == 200, \
        "POST /api/projects/1/archive with confirmation must succeed, got %s" % status
    assert archived["archived"] is True, archived

    status, _, got = call(app, "GET", "/api/projects/1")
    assert got["archived"] is True, got

    status, page, _ = call(app, "GET", "/")
    assert "Helios" not in page, "archived projects must leave the dashboard"


def test_archive_requires_confirmation():
    app = create_app()
    call(app, "POST", "/api/projects", {"name": "Icarus"})

    status, _, err = call(app, "POST", "/api/projects/1/archive", {})
    assert status == 400, \
        "archive without {'confirm': true} must be rejected with 400, got %s" % status

    status, _, err = call(app, "POST", "/api/projects/1/archive")
    assert status == 400, \
        "archive with no body must be rejected with 400, got %s" % status

    _, _, got = call(app, "GET", "/api/projects/1")
    assert got["archived"] is False, \
        "a rejected archive call must not archive the project"


def main():
    tests = [
        test_project_and_item_crud,
        test_dashboard_counts_follow_item_writes,
        test_route_table_lists_every_declared_route,
        test_rename_shows_up_everywhere,
        test_archive_endpoint_is_reachable,
        test_archive_requires_confirmation,
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print("ok   %s" % test.__name__)
        except AssertionError as exc:
            failures += 1
            print("FAIL %s: %s" % (test.__name__, exc))
    if failures:
        raise SystemExit("%d test(s) failed" % failures)
    print("all %d tests passed" % len(tests))


if __name__ == "__main__":
    main()
