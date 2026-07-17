"""Acceptance tests for the versioned config migrator. Run: python3 test_confmigrate.py"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

V1 = {"version": 1, "server": "db.internal:5432", "debug": True,
      "log_file": "/var/log/app.log"}
V4 = {"version": 4, "host": "db.internal", "port": 5432,
      "logging": {"level": "debug", "file": "/var/log/app.log"}}


def cli(*args):
    return subprocess.run([sys.executable, "confmigrate.py", *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def check_library():
    from confmig import engine
    from confmig.registry import migration

    assert engine.LATEST == 4, engine.LATEST

    # the full chain, and purity: the input dict must not be mutated
    src = copy.deepcopy(V1)
    out = engine.upgrade(src)
    assert out == V4, out
    assert src == V1, "upgrade() must not mutate its input"

    # stop at an intermediate version
    v2 = {"version": 2, "host": "cache.internal", "port": 6379,
          "debug": False, "log_file": "cache.log"}
    out = engine.upgrade(copy.deepcopy(v2), 3)
    assert out == {"version": 3, "host": "cache.internal", "port": 6379,
                   "log_level": "info", "log_file": "cache.log"}, out

    # already at target is a no-op that still returns the config
    out = engine.upgrade(copy.deepcopy(V4), 4)
    assert out == V4, out

    # downgrades are refused
    try:
        engine.upgrade(copy.deepcopy(V4), 2)
        raise AssertionError("downgrade must raise DowngradeError")
    except engine.DowngradeError:
        pass

    # a bad starting config is caught before any step runs
    bad = {"version": 1, "server": "no-port-here", "debug": True,
           "log_file": "x.log"}
    try:
        engine.upgrade(bad)
        raise AssertionError("invalid v1 must raise ValidationError")
    except engine.ValidationError as e:
        assert "v1" in str(e), str(e)

    bad2 = {"version": 2, "host": "h", "port": "8080", "debug": False,
            "log_file": "x.log"}
    try:
        engine.upgrade(bad2, 3)
        raise AssertionError("a string port must fail v2 validation")
    except engine.ValidationError as e:
        assert "v2" in str(e), str(e)

    # unreachable target: no registered step goes that far
    try:
        engine.upgrade(copy.deepcopy(V1), 9)
        raise AssertionError("upgrading past LATEST must raise LookupError")
    except LookupError:
        pass

    # the registry refuses duplicate steps
    try:
        migration(1)(lambda cfg: cfg)
        raise AssertionError("a second registration for v1 must raise ValueError")
    except ValueError:
        pass

    # the version field must be an int >= 1
    for broken in ({}, {"version": "two"}, {"version": 0}):
        try:
            engine.upgrade(dict(broken))
            raise AssertionError(f"{broken!r} must raise ValueError")
        except ValueError:
            pass


def check_cli(tmp):
    # a real v1 file with crusty formatting: the backup must be byte-exact
    app = os.path.join(tmp, "app.json")
    original = ('{ "version": 1,\n      "server": "db.internal:5432",\n'
                '  "debug": true, "log_file": "/var/log/app.log" }\n\n')
    with open(app, "w") as f:
        f.write(original)

    r = cli("upgrade", app)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert r.stdout.strip() == f"migrated {app}: v1 -> v4", r.stdout
    with open(app) as f:
        assert json.load(f) == V4
    with open(app + ".bak", "rb") as f:
        assert f.read() == original.encode(), \
            "backup must hold the pre-migration bytes exactly"

    # a second run is a no-op and must not rewrite the backup
    with open(app, "rb") as f:
        migrated_bytes = f.read()
    with open(app + ".bak", "wb") as f:
        f.write(b"SENTINEL")
    r = cli("upgrade", app)
    assert r.returncode == 0 and r.stdout.strip() == "already at v4", \
        (r.returncode, r.stdout)
    with open(app + ".bak", "rb") as f:
        assert f.read() == b"SENTINEL", "a no-op run must not touch the backup"
    with open(app, "rb") as f:
        assert f.read() == migrated_bytes, "a no-op run must not rewrite the config"

    # partial upgrade with --to, then a refused downgrade
    staged = os.path.join(tmp, "staged.json")
    v1b = {"version": 1, "server": "queue.internal:5672", "debug": False,
           "log_file": "queue.log"}
    with open(staged, "w") as f:
        json.dump(v1b, f)
    r = cli("upgrade", staged, "--to", "3")
    assert r.returncode == 0 and r.stdout.strip() == f"migrated {staged}: v1 -> v3", \
        (r.returncode, r.stdout, r.stderr)
    with open(staged) as f:
        got = json.load(f)
    assert got == {"version": 3, "host": "queue.internal", "port": 5672,
                   "log_level": "info", "log_file": "queue.log"}, got
    with open(staged + ".bak", "rb") as f:
        assert f.read() == json.dumps(v1b).encode(), "backup is the original bytes"

    with open(staged, "rb") as f:
        before = f.read()
    r = cli("upgrade", staged, "--to", "2")
    assert r.returncode == 1 and "downgrade" in r.stderr.lower(), \
        (r.returncode, r.stderr)
    with open(staged, "rb") as f:
        assert f.read() == before, "a refused downgrade must not touch the file"

    # invalid config: exit 1, file untouched, and no backup left behind
    corrupt = os.path.join(tmp, "corrupt.json")
    body = json.dumps({"version": 2, "host": "h", "port": "8080",
                       "debug": True, "log_file": "x.log"})
    with open(corrupt, "w") as f:
        f.write(body)
    r = cli("upgrade", corrupt)
    assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)
    with open(corrupt) as f:
        assert f.read() == body, "failed migrations must not modify the file"
    assert not os.path.exists(corrupt + ".bak"), \
        "failed migrations must not leave a backup"

    # a file newer than anything we know is a refusal, not an 'upgrade' down
    future = os.path.join(tmp, "future.json")
    with open(future, "w") as f:
        json.dump({"version": 9, "whatever": True}, f)
    r = cli("upgrade", future)
    assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)

    # garbage JSON and missing files are runtime errors (exit 1)...
    garbled = os.path.join(tmp, "garbled.json")
    with open(garbled, "w") as f:
        f.write("{ not json")
    r = cli("upgrade", garbled)
    assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)
    r = cli("upgrade", os.path.join(tmp, "ghost.json"))
    assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)

    # ...while a malformed invocation is a usage error (exit 2)
    r = cli("upgrade", app, "--to", "banana")
    assert r.returncode == 2, (r.returncode, r.stderr)

    # the version subcommand
    r = cli("version", app)
    assert r.returncode == 0 and r.stdout.strip() == "v4", (r.returncode, r.stdout)
    r = cli("version", garbled)
    assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)


def main():
    tmp = tempfile.mkdtemp(dir=".")
    try:
        check_library()
        check_cli(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all confmigrate checks passed")


if __name__ == "__main__":
    main()
