"""Acceptance tests for the make-style task runner. Run: python3 test_runner.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
PY = sys.executable


def runner(taskfile, *args):
    return subprocess.run([sys.executable, "runner.py", "--file", taskfile, *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def touch_cmd(name):
    return [PY, "-c", f"open({name!r}, 'w').write('1')"]


def taskfile(dirpath, tasks):
    path = os.path.join(dirpath, "build.json")
    with open(path, "w") as f:
        json.dump({"tasks": tasks}, f)
    return path


def main():
    tmp = tempfile.mkdtemp(dir=".")
    try:
        # ---- diamond: deps first, shared dep once, ready set alphabetical
        d1 = os.path.join(tmp, "d1")
        os.makedirs(d1)
        tf = taskfile(d1, {
            "gen":       {"cmd": touch_cmd("gen.marker")},
            "lib_a":     {"cmd": touch_cmd("lib_a.marker"), "deps": ["gen"]},
            "lib_b":     {"cmd": touch_cmd("lib_b.marker"), "deps": ["gen"]},
            "app":       {"cmd": touch_cmd("app.marker"), "deps": ["lib_a", "lib_b"]},
            "unrelated": {"cmd": touch_cmd("unrelated.marker")},
        })
        r = runner(tf, "app")
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert r.stdout.splitlines() == ["run gen", "run lib_a", "run lib_b", "run app"], r.stdout
        # commands execute with the taskfile's directory as cwd
        for m in ["gen.marker", "lib_a.marker", "lib_b.marker", "app.marker"]:
            assert os.path.exists(os.path.join(d1, m)), f"{m} missing (wrong cwd?)"
        assert not os.path.exists(os.path.join(d1, "unrelated.marker")), \
            "tasks outside the target closure must not run"

        # ---- readiness order is dependency-driven, not just alphabetical
        d2 = os.path.join(tmp, "d2")
        os.makedirs(d2)
        tf = taskfile(d2, {
            "a_second": {"cmd": touch_cmd("a.marker"), "deps": ["z_first"]},
            "z_first":  {"cmd": touch_cmd("z.marker")},
            "top":      {"cmd": touch_cmd("top.marker"), "deps": ["a_second", "z_first"]},
        })
        r = runner(tf, "top")
        assert r.stdout.splitlines() == ["run z_first", "run a_second", "run top"], r.stdout

        # ---- mtime-based up-to-date skipping
        d3 = os.path.join(tmp, "d3")
        os.makedirs(d3)
        concat = [PY, "-c",
                  "open('bundle.out', 'w').write(open('a.txt').read() + open('b.txt').read())"]
        tf = taskfile(d3, {
            "bundle": {"cmd": concat, "inputs": ["a.txt", "b.txt"], "outputs": ["bundle.out"]},
        })
        with open(os.path.join(d3, "a.txt"), "w") as f:
            f.write("A")
        with open(os.path.join(d3, "b.txt"), "w") as f:
            f.write("B")
        r = runner(tf, "bundle")
        assert r.stdout.splitlines() == ["run bundle"], r.stdout
        out = os.path.join(d3, "bundle.out")
        with open(out) as f:
            assert f.read() == "AB"

        # outputs newer than inputs -> skip, and the command really does not run
        with open(out, "w") as f:
            f.write("SENTINEL")
        os.utime(os.path.join(d3, "a.txt"), (1_000_000, 1_000_000))
        os.utime(os.path.join(d3, "b.txt"), (1_000_000, 1_000_000))
        os.utime(out, (2_000_000, 2_000_000))
        r = runner(tf, "bundle")
        assert r.stdout.splitlines() == ["skip bundle (up to date)"], r.stdout
        with open(out) as f:
            assert f.read() == "SENTINEL", "skipped task must not execute"

        # one input newer than the oldest output -> runs again
        os.utime(os.path.join(d3, "b.txt"), (3_000_000, 3_000_000))
        r = runner(tf, "bundle")
        assert r.stdout.splitlines() == ["run bundle"], r.stdout
        with open(out) as f:
            assert f.read() == "AB"

        # missing output -> runs; missing input -> runs (never "up to date")
        os.remove(out)
        r = runner(tf, "bundle")
        assert r.stdout.splitlines() == ["run bundle"], r.stdout
        os.utime(os.path.join(d3, "a.txt"), (1_000_000, 1_000_000))
        os.utime(os.path.join(d3, "b.txt"), (1_000_000, 1_000_000))
        os.utime(out, (2_000_000, 2_000_000))
        tf2 = taskfile(d3, {
            "bundle": {"cmd": concat, "inputs": ["a.txt", "b.txt", "ghost.txt"],
                       "outputs": ["bundle.out"]},
        })
        r = runner(tf2, "bundle")
        assert r.stdout.splitlines() == ["run bundle"], ("missing input must force a run", r.stdout)

        # ---- pure mtime semantics: a freshly-run dep does not dirty its dependent
        d4 = os.path.join(tmp, "d4")
        os.makedirs(d4)
        pack_cmd = [PY, "-c", "open('packed.out', 'w').write(open('a.txt').read())"]
        tf = taskfile(d4, {
            "stamp": {"cmd": touch_cmd("stamp.marker")},  # no outputs: always runs
            "pack":  {"cmd": pack_cmd, "deps": ["stamp"],
                      "inputs": ["a.txt"], "outputs": ["packed.out"]},
        })
        with open(os.path.join(d4, "a.txt"), "w") as f:
            f.write("A")
        with open(os.path.join(d4, "packed.out"), "w") as f:
            f.write("OLD")
        os.utime(os.path.join(d4, "a.txt"), (1_000_000, 1_000_000))
        os.utime(os.path.join(d4, "packed.out"), (2_000_000, 2_000_000))
        r = runner(tf, "pack")
        assert r.stdout.splitlines() == ["run stamp", "skip pack (up to date)"], r.stdout
        with open(os.path.join(d4, "packed.out")) as f:
            assert f.read() == "OLD"
        # and it runs every time it's asked (no outputs declared)
        r = runner(tf, "stamp")
        assert r.stdout.splitlines() == ["run stamp"], r.stdout

        # ---- dry run: same plan, nothing executes
        d5 = os.path.join(tmp, "d5")
        os.makedirs(d5)
        tf = taskfile(d5, {
            "gen": {"cmd": touch_cmd("gen.marker")},
            "app": {"cmd": touch_cmd("app.marker"), "deps": ["gen"]},
        })
        r = runner(tf, "--dry-run", "app")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == ["would run gen", "would run app"], r.stdout
        assert not os.path.exists(os.path.join(d5, "gen.marker"))
        assert not os.path.exists(os.path.join(d5, "app.marker"))
        # dry run still reports up-to-date skips
        r = runner(taskfile(d3, {
            "bundle": {"cmd": concat, "inputs": ["a.txt", "b.txt"], "outputs": ["bundle.out"]},
        }), "--dry-run", "bundle")
        assert r.stdout.splitlines() == ["skip bundle (up to date)"], r.stdout

        # ---- failure stops the line
        d6 = os.path.join(tmp, "d6")
        os.makedirs(d6)
        tf = taskfile(d6, {
            "boom":  {"cmd": [PY, "-c", "import sys; sys.exit(3)"]},
            "after": {"cmd": touch_cmd("after.marker"), "deps": ["boom"]},
        })
        r = runner(tf, "after")
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert "run boom" in r.stdout.splitlines(), r.stdout
        assert "run after" not in r.stdout, r.stdout
        assert "boom" in r.stderr, r.stderr
        assert not os.path.exists(os.path.join(d6, "after.marker"))

        # ---- graph and file errors
        d7 = os.path.join(tmp, "d7")
        os.makedirs(d7)
        tf = taskfile(d7, {
            "x": {"cmd": touch_cmd("x.marker"), "deps": ["y"]},
            "y": {"cmd": touch_cmd("y.marker"), "deps": ["x"]},
        })
        r = runner(tf, "x")
        assert r.returncode == 1 and "cycle" in r.stderr.lower(), (r.returncode, r.stderr)

        r = runner(tf, "nosuchtask")
        assert r.returncode == 1 and "nosuchtask" in r.stderr, (r.returncode, r.stderr)

        tf = taskfile(d7, {"p": {"cmd": touch_cmd("p.marker"), "deps": ["ghost"]}})
        r = runner(tf, "p")
        assert r.returncode == 1 and "ghost" in r.stderr, (r.returncode, r.stderr)

        r = runner(os.path.join(tmp, "missing.json"), "app")
        assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)
        badjson = os.path.join(tmp, "bad.json")
        with open(badjson, "w") as f:
            f.write("{not json")
        r = runner(badjson, "app")
        assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all runner checks passed")


if __name__ == "__main__":
    main()
