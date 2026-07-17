"""Acceptance tests for logreport. Run: python3 test_logreport.py"""
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def report(*args):
    return subprocess.run([sys.executable, "logreport.py", *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def main():
    tmp = tempfile.mkdtemp(dir=".")
    try:
        # ---- summary over the checked-in fixture
        r = report("summary", "access.log")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == [
            "requests: 20",
            "bytes: 71400",
            "malformed: 2",
            "status 200: 12",
            "status 201: 2",
            "status 302: 1",
            "status 404: 3",
            "status 500: 2",
        ], r.stdout

        # ---- top paths: count desc, ties by path ascending, query strings stripped
        r = report("top", "access.log", "--by", "path", "-n", "3")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == [
            "5 /api/users",
            "5 /index.html",
            "3 /api/orders",
        ], r.stdout

        # default is top 10 (fixture has 8 distinct paths, all shown)
        r = report("top", "access.log", "--by", "path")
        assert r.stdout.splitlines() == [
            "5 /api/users",
            "5 /index.html",
            "3 /api/orders",
            "2 /favicon.ico",
            "2 /static/app.js",
            "1 /health",
            "1 /old-page",
            "1 /robots.txt",
        ], r.stdout

        # ---- top ips
        r = report("top", "access.log", "--by", "ip", "-n", "2")
        assert r.stdout.splitlines() == ["8 10.0.0.1", "6 10.0.0.2"], r.stdout

        # ---- filters
        r = report("summary", "access.log", "--status", "404")
        assert r.stdout.splitlines() == [
            "requests: 3",
            "bytes: 620",
            "malformed: 2",
            "status 404: 3",
        ], ("malformed count ignores filters", r.stdout)

        r = report("summary", "access.log", "--method", "POST")
        assert r.stdout.splitlines() == [
            "requests: 4",
            "bytes: 620",
            "malformed: 2",
            "status 201: 2",
            "status 500: 2",
        ], r.stdout

        r = report("top", "access.log", "--by", "path", "--method", "POST", "-n", "5")
        assert r.stdout.splitlines() == ["2 /api/orders", "2 /api/users"], r.stdout

        r = report("top", "access.log", "--by", "path", "--status", "200", "-n", "2")
        assert r.stdout.splitlines() == ["5 /index.html", "3 /api/users"], r.stdout

        # --path-prefix matches the query-stripped path
        r = report("top", "access.log", "--by", "ip", "--path-prefix", "/api")
        assert r.stdout.splitlines() == [
            "3 10.0.0.1",
            "3 10.0.0.2",
            "1 172.16.0.5",
            "1 192.168.1.9",
        ], r.stdout

        # combined filters
        r = report("summary", "access.log", "--path-prefix", "/api", "--status", "500")
        assert r.stdout.splitlines() == [
            "requests: 2",
            "bytes: 460",
            "malformed: 2",
            "status 500: 2",
        ], r.stdout

        # a filter that matches nothing: zeros, no status lines, exit 0
        r = report("summary", "access.log", "--status", "418")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == ["requests: 0", "bytes: 0", "malformed: 2"], r.stdout
        r = report("top", "access.log", "--by", "path", "--status", "418")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout)

        # ---- synthetic log: '-' bytes count as 0, ?query variants collapse
        mini = os.path.join(tmp, "mini.log")
        with open(mini, "w") as f:
            f.write('1.1.1.1 - - [01/Jan/2026:00:00:00 +0000] "GET /a?x=1 HTTP/1.1" 200 10\n')
            f.write('1.1.1.1 - - [01/Jan/2026:00:00:01 +0000] "GET /a?x=2 HTTP/1.1" 200 -\n')
            f.write('1.1.1.1 - - [01/Jan/2026:00:00:02 +0000] "GET /a HTTP/1.1" 200 5\n')
        r = report("summary", mini)
        assert r.stdout.splitlines() == [
            "requests: 3",
            "bytes: 15",
            "malformed: 0",
            "status 200: 3",
        ], r.stdout
        r = report("top", mini, "--by", "path")
        assert r.stdout.splitlines() == ["3 /a"], r.stdout

        # a file that is nothing but junk still summarizes cleanly
        junk = os.path.join(tmp, "junk.log")
        with open(junk, "w") as f:
            f.write("!!!\n\x00\x01\n")
        r = report("summary", junk)
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == ["requests: 0", "bytes: 0", "malformed: 2"], r.stdout

        # ---- usage errors: exit 2 with stderr
        r = report("summary", os.path.join(tmp, "no-such.log"))
        assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)
        r = report("top", "access.log", "--by", "verb")
        assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all logreport checks passed")


if __name__ == "__main__":
    main()
