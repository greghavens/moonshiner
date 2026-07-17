"""Acceptance tests for the notes API server. Run: python3 test_notes_api.py"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}


def start_server(db, servers):
    proc = subprocess.Popen(
        [sys.executable, "server.py", "--db", db, "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV)
    servers.append(proc)
    box = {}

    def _read():
        box["line"] = proc.stdout.readline()

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(15)
    line = box.get("line", "")
    m = re.search(r"listening on 127\.0\.0\.1:(\d+)", line or "")
    if not m:
        proc.kill()
        _, err = proc.communicate(timeout=10)
        raise AssertionError(
            "first stdout line must be 'listening on 127.0.0.1:<port>', "
            f"got {line!r}; stderr: {err[:800]}")
    return f"http://127.0.0.1:{m.group(1)}"


def stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def call(method, url, payload=None, headers=None, raw=None):
    data = raw if raw is not None else (
        None if payload is None else json.dumps(payload).encode())
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            return resp.status, dict(resp.headers), json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = None
        return e.code, dict(e.headers), parsed


def main():
    tmp = tempfile.mkdtemp(dir=".")
    db = os.path.join(tmp, "notes.json")
    servers = []
    try:
        base = start_server(db, servers)

        # empty store lists as [] with a JSON content type
        st, hdr, body = call("GET", base + "/notes")
        assert st == 200, st
        assert body == [], body
        assert hdr.get("Content-Type", "").startswith("application/json"), \
            hdr.get("Content-Type")

        # create notes; ids count up from 1, versions start at 1
        st, _, n1 = call("POST", base + "/notes",
                         {"title": "standup notes", "body": "discuss rollout plan"})
        assert st == 201, st
        assert n1["id"] == 1 and n1["version"] == 1, n1
        assert n1["title"] == "standup notes" and n1["body"] == "discuss rollout plan", n1
        st, _, n2 = call("POST", base + "/notes",
                         {"title": "grocery run", "body": "eggs, coffee beans"})
        assert st == 201 and n2["id"] == 2 and n2["version"] == 1, (st, n2)
        st, _, n3 = call("POST", base + "/notes",
                         {"title": "Rollout checklist", "body": "flip the flag, watch dashboards"})
        assert st == 201 and n3["id"] == 3, (st, n3)

        # fetch one: ETag carries the version, quoted
        st, hdr, got = call("GET", base + "/notes/1")
        assert st == 200 and got["id"] == 1 and got["version"] == 1, (st, got)
        assert hdr.get("ETag") == '"1"', hdr.get("ETag")

        # the collection is sorted by id
        st, _, notes = call("GET", base + "/notes")
        assert [n["id"] for n in notes] == [1, 2, 3], notes

        # ?q= searches title and body, case-insensitively, results still by id
        st, _, hits = call("GET", base + "/notes?q=rollout")
        assert st == 200 and [n["id"] for n in hits] == [1, 3], hits
        st, _, hits = call("GET", base + "/notes?q=COFFEE")
        assert [n["id"] for n in hits] == [2], hits
        st, _, hits = call("GET", base + "/notes?q=zebra")
        assert hits == [], hits

        # updates demand optimistic concurrency
        st, _, _ = call("PUT", base + "/notes/1",
                        {"title": "standup notes", "body": "rollout done"})
        assert st == 428, ("PUT without If-Match", st)
        st, _, _ = call("PUT", base + "/notes/1",
                        {"title": "standup notes", "body": "rollout done"},
                        headers={"If-Match": '"7"'})
        assert st == 412, ("PUT with wrong If-Match", st)
        st, _, got = call("GET", base + "/notes/1")
        assert got["body"] == "discuss rollout plan" and got["version"] == 1, \
            ("rejected PUTs must not change the note", got)

        st, hdr, upd = call("PUT", base + "/notes/1",
                            {"title": "standup notes", "body": "rollout done"},
                            headers={"If-Match": '"1"'})
        assert st == 200 and upd["version"] == 2 and upd["body"] == "rollout done", (st, upd)
        assert hdr.get("ETag") == '"2"', hdr.get("ETag")

        # the old ETag is now stale: lost-update protection
        st, _, _ = call("PUT", base + "/notes/1",
                        {"title": "standup notes", "body": "overwrite attempt"},
                        headers={"If-Match": '"1"'})
        assert st == 412, st
        st, _, got = call("GET", base + "/notes/1")
        assert got["body"] == "rollout done" and got["version"] == 2, got

        # unknown ids and bad payloads
        st, _, err = call("GET", base + "/notes/99")
        assert st == 404, st
        assert isinstance(err, dict) and "error" in err, \
            ("error responses are JSON objects with an 'error' key", err)
        st, _, _ = call("PUT", base + "/notes/99", {"title": "x", "body": "y"},
                        headers={"If-Match": '"1"'})
        assert st == 404, st
        st, _, err = call("POST", base + "/notes", {"body": "no title here"})
        assert st == 400, ("POST without title", st)
        assert isinstance(err, dict) and "error" in err, err
        st, _, _ = call("POST", base + "/notes", {"title": "", "body": "x"})
        assert st == 400, ("POST with empty title", st)
        st, _, _ = call("POST", base + "/notes", raw=b"{this is not json")
        assert st == 400, ("POST with broken JSON", st)
        st, _, _ = call("PUT", base + "/notes/1", raw=b"also not json",
                        headers={"If-Match": '"2"'})
        assert st == 400, st
        st, _, _ = call("GET", base + "/nope")
        assert st == 404, st

        # delete
        st, _, _ = call("DELETE", base + "/notes/2")
        assert st == 204, st
        st, _, _ = call("GET", base + "/notes/2")
        assert st == 404, st
        st, _, _ = call("DELETE", base + "/notes/2")
        assert st == 404, st
        st, _, notes = call("GET", base + "/notes")
        assert [n["id"] for n in notes] == [1, 3], notes

        # kill the server; a fresh process on the same --db must see everything
        stop(servers[0])
        base = start_server(db, servers)
        st, _, notes = call("GET", base + "/notes")
        assert st == 200 and [n["id"] for n in notes] == [1, 3], \
            ("notes must survive a restart", notes)
        st, hdr, got = call("GET", base + "/notes/1")
        assert got["body"] == "rollout done" and got["version"] == 2, got
        assert hdr.get("ETag") == '"2"', hdr.get("ETag")

        # deleted ids are never reused, even across restarts
        st, _, n4 = call("POST", base + "/notes",
                         {"title": "retro agenda", "body": "what slowed the rollout"})
        assert st == 201 and n4["id"] == 4, ("id 2 stays retired", n4)

        # search still works on the reloaded store
        st, _, hits = call("GET", base + "/notes?q=rollout")
        assert [n["id"] for n in hits] == [1, 3, 4], hits
    finally:
        for p in servers:
            stop(p)
        shutil.rmtree(tmp, ignore_errors=True)
    print("all notes-api checks passed")


if __name__ == "__main__":
    main()
