"""Acceptance tests for the plugin text pipeline. Run: python3 test_textpipe.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def cli(*args):
    return subprocess.run([sys.executable, "textpipe.py", *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


def check_library():
    from pipeline.registry import register, get, names
    from pipeline.engine import apply

    # built-ins are registered as a side effect of importing the package
    for name in ["strip_trailing", "tabs_to_spaces", "squeeze_blanks",
                 "ensure_final_newline"]:
        assert name in names(), (name, names())
    assert names() == sorted(names()), "names() must be sorted"

    # plugins are plain decorated functions: f(text, **options)
    @register("shout", options={"suffix": "!"})
    def shout(text, suffix):
        return text.upper() + suffix

    assert get("shout") is shout, "the decorator must return the function unchanged"
    assert apply("hi", [{"plugin": "shout"}]) == "HI!", \
        "option defaults come from the decorator"
    assert apply("hi", [{"plugin": "shout", "options": {"suffix": "?"}}]) == "HI?"

    # duplicate names are a registration error
    try:
        register("shout")(lambda text: text)
        raise AssertionError("re-registering 'shout' must raise ValueError")
    except ValueError:
        pass

    # unknown plugin / undeclared option
    try:
        apply("x", [{"plugin": "does_not_exist"}])
        raise AssertionError("unknown plugin must raise LookupError")
    except LookupError:
        pass
    try:
        apply("x", [{"plugin": "shout", "options": {"volume": 11}}])
        raise AssertionError("an option the plugin didn't declare must raise ValueError")
    except ValueError:
        pass

    # built-in behavior
    assert apply("a\tb\n", [{"plugin": "tabs_to_spaces"}]) == "a   b\n"
    assert apply("ab\tc\n", [{"plugin": "tabs_to_spaces"}]) == "ab  c\n", \
        "tab stops, not a flat four spaces"
    assert apply("a\tb\n",
                 [{"plugin": "tabs_to_spaces", "options": {"width": 2}}]) == "a b\n"
    assert apply("x  \n\ty\t\n", [{"plugin": "strip_trailing"}]) == "x\n\ty\n"
    assert apply("x\n\n\n\n\ny\n", [{"plugin": "squeeze_blanks"}]) == "x\n\ny\n"
    assert apply("x\n\n\n\n\ny\n",
                 [{"plugin": "squeeze_blanks", "options": {"max": 2}}]) == "x\n\n\ny\n"
    assert apply("x", [{"plugin": "ensure_final_newline"}]) == "x\n"
    assert apply("x\n\n", [{"plugin": "ensure_final_newline"}]) == "x\n"
    assert apply("", [{"plugin": "ensure_final_newline"}]) == ""

    # steps run in list order; 'blank' means empty, not whitespace-only
    messy = "a\n\n  \n\nb\n"
    assert apply(messy, [{"plugin": "strip_trailing"},
                         {"plugin": "squeeze_blanks"}]) == "a\n\nb\n"
    assert apply(messy, [{"plugin": "squeeze_blanks"},
                         {"plugin": "strip_trailing"}]) == "a\n\n\n\nb\n"


def check_cli(tmp):
    cfg = os.path.join(tmp, "pipe.json")
    write(cfg, json.dumps({"steps": [
        {"plugin": "strip_trailing"},
        {"plugin": "squeeze_blanks"},
        {"plugin": "ensure_final_newline"},
    ]}))
    f1 = os.path.join(tmp, "notes.txt")
    f2 = os.path.join(tmp, "clean.txt")
    write(f1, "top   \n\n  \n\nmiddle\t\nend")
    write(f2, "already clean\n")

    # dry run: unified diffs only, nothing written, exit 1 while dirty
    before = read(f1)
    r = cli("--config", cfg, "--dry-run", f1, f2)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert read(f1) == before, "--dry-run must not modify files"
    assert ("--- a/" + f1) in r.stdout, r.stdout
    assert ("+++ b/" + f1) in r.stdout, r.stdout
    assert "-top   \n" in r.stdout and "+top\n" in r.stdout, r.stdout
    assert ("--- a/" + f2) not in r.stdout, \
        ("a clean file must produce no diff", r.stdout)

    # apply mode rewrites in place and reports per file, in argv order
    r = cli("--config", cfg, f1, f2)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert r.stdout.splitlines() == ["rewrote " + f1, "ok " + f2], r.stdout
    assert read(f1) == "top\n\nmiddle\nend\n", repr(read(f1))

    # now everything is clean: dry run exits 0 with no output
    r = cli("--config", cfg, "--dry-run", f1, f2)
    assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout)
    r = cli("--config", cfg, f1, f2)
    assert r.stdout.splitlines() == ["ok " + f1, "ok " + f2], r.stdout

    # extra plugins load from --plugins so configs can use them
    extra = os.path.join(tmp, "extra.py")
    write(extra,
          "from pipeline.registry import register\n"
          "\n"
          "@register('banner', options={'note': 'reviewed'})\n"
          "def banner(text, note):\n"
          "    return '# ' + note + '\\n' + text\n")
    cfg2 = os.path.join(tmp, "pipe2.json")
    write(cfg2, json.dumps(
        {"steps": [{"plugin": "banner", "options": {"note": "checked by pipeline"}}]}))
    r = cli("--config", cfg2, "--plugins", extra, f2)
    assert r.returncode == 0 and r.stdout.splitlines() == ["rewrote " + f2], \
        (r.returncode, r.stdout, r.stderr)
    assert read(f2) == "# checked by pipeline\nalready clean\n", repr(read(f2))

    # ...but without --plugins the same config is an unknown-plugin error
    r = cli("--config", cfg2, f2)
    assert r.returncode == 2 and "banner" in r.stderr, (r.returncode, r.stderr)
    assert read(f2) == "# checked by pipeline\nalready clean\n", \
        "failed runs must not write"

    # an undeclared option is a config error
    cfg3 = os.path.join(tmp, "pipe3.json")
    write(cfg3, json.dumps(
        {"steps": [{"plugin": "squeeze_blanks", "options": {"depth": 3}}]}))
    r = cli("--config", cfg3, f1)
    assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)

    # a missing input file fails up front: no file gets modified
    write(f1, "dirty   \n")
    r = cli("--config", cfg, f1, os.path.join(tmp, "missing.txt"))
    assert r.returncode == 2 and "missing.txt" in r.stderr, (r.returncode, r.stderr)
    assert read(f1) == "dirty   \n", "no file may change when any input is missing"


def main():
    tmp = tempfile.mkdtemp(dir=".")
    try:
        check_library()
        check_cli(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all textpipe checks passed")


if __name__ == "__main__":
    main()
