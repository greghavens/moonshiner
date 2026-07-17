"""Acceptance tests for the changelog compiler CLI. Run: python3 test_changelog.py"""
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

PENDING = {
    "add-json.txt": "feat(cli): add --json flag\n",
    "bad-flags.txt": "fix(cli): exit nonzero on bad flags\n",
    "onboarding.txt": "feat: polish onboarding copy\n",
    "token-auth.txt": ("feat(api): new token auth\n"
                       "\n"
                       "rolled out behind a flag\n"
                       "\n"
                       "BREAKING CHANGE: v1 tokens are no longer accepted\n"),
    "split-models.txt": "refactor(db)!: split read and write models\n",
    "install-guide.txt": "docs(readme): rewrite install guide\n",
    "prepared.txt": "perf(query): cache prepared statements\n",
}

RENDERED = """## 2.0.0

### Breaking Changes

- **api**: new token auth
- **db**: split read and write models

### Features

- polish onboarding copy
- **cli**: add --json flag

### Fixes

- **cli**: exit nonzero on bad flags

### Performance

- **query**: cache prepared statements

### Maintenance

- **readme**: rewrite install guide
"""


def cli(*args):
    return subprocess.run(
        [sys.executable, "cli.py", *args],
        capture_output=True, text=True, env=ENV, timeout=30)


def ok(*args):
    p = cli(*args)
    assert p.returncode == 0, (args, p.returncode, p.stderr)
    return p.stdout


def fail(*args):
    p = cli(*args)
    assert p.returncode != 0, (args, p.stdout)
    return p.stderr


def make_dir(tmp, name, files):
    path = os.path.join(tmp, name)
    os.makedirs(path, exist_ok=True)
    for fname, text in files.items():
        with open(os.path.join(path, fname), "w", encoding="utf-8") as fh:
            fh.write(text)
    return path


def test_entry_parsing():
    import entries

    e = entries.parse_entry("feat(cli): add --json flag\n")
    assert (e["type"], e["scope"], e["subject"], e["breaking"]) == \
        ("feat", "cli", "add --json flag", False)
    # scope is optional, bang means breaking
    e = entries.parse_entry("fix!: reject empty ids")
    assert (e["type"], e["scope"], e["breaking"]) == ("fix", None, True)
    # the footer marks breaking too
    e = entries.parse_entry(
        "feat(api): new auth\n\nsome body text\n\nBREAKING CHANGE: v1 is gone\n")
    assert e["breaking"] is True
    # trailing whitespace around the subject is noise, not signal
    assert entries.parse_entry("fix(io):   flush on close  \n")["subject"] == \
        "flush on close"

    for bad in ["feature(cli): everything", "feat add thing", "feat(cli):",
                "(cli): no type", "just some prose"]:
        try:
            entries.parse_entry(bad)
            assert False, ("expected ValueError", bad)
        except ValueError:
            pass


def test_semver_math():
    import semver

    assert semver.parse("1.4.2") == (1, 4, 2)
    for bad in ["1.4", "v1.4.2", "1.4.2-rc1", "1.4.two", ""]:
        try:
            semver.parse(bad)
            assert False, ("expected ValueError", bad)
        except ValueError:
            pass

    s = semver.suggest
    # breaking > feature > fix; bumps reset the fields below them
    assert s("1.4.2", True, True, True) == "2.0.0"
    assert s("1.4.2", False, True, True) == "1.5.0"
    assert s("1.4.2", False, False, True) == "1.4.3"
    # nothing releasable: the version stands still
    assert s("1.4.2", False, False, False) == "1.4.2"
    # pre-1.0 rule: breaking bumps minor, features bump patch
    assert s("0.3.1", True, False, False) == "0.4.0"
    assert s("0.3.1", False, True, False) == "0.3.2"
    assert s("0.3.1", False, False, True) == "0.3.2"


def test_bump_and_render(tmp):
    pending = make_dir(tmp, "pending", PENDING)
    # a stray non-.txt file in the directory is not an entry
    with open(os.path.join(pending, "notes.md"), "w") as fh:
        fh.write("this is not a changeset\n")

    assert ok("bump", pending, "--version", "1.4.2").strip() == "2.0.0"
    assert ok("bump", pending, "--version", "0.3.1").strip() == "0.4.0"
    assert ok("render", pending, "--version", "1.4.2") == RENDERED

    # fixes only -> patch, and empty sections are omitted entirely
    fixes = make_dir(tmp, "fixes-only", {
        "epipe.txt": "fix(cli): handle EPIPE\n"})
    assert ok("bump", fixes, "--version", "1.4.2").strip() == "1.4.3"
    assert ok("render", fixes, "--version", "1.4.2") == (
        "## 1.4.3\n\n### Fixes\n\n- **cli**: handle EPIPE\n")

    # chores alone don't move the version
    chores = make_dir(tmp, "chores-only", {"deps.txt": "chore: bump deps\n"})
    assert ok("bump", chores, "--version", "1.4.2").strip() == "1.4.2"

    # one malformed entry poisons the whole run, and is named
    poisoned = make_dir(tmp, "poisoned", dict(PENDING))
    with open(os.path.join(poisoned, "bad.txt"), "w") as fh:
        fh.write("feature: everything, everywhere\n")
    err = fail("bump", poisoned, "--version", "1.4.2")
    assert "bad.txt" in err
    fail("render", poisoned, "--version", "1.4.2")

    # a version that isn't X.Y.Z is refused
    fail("bump", pending, "--version", "1.4")
    fail("render", pending, "--version", "v1.4.2")


def test_release(tmp):
    changelog = os.path.join(tmp, "CHANGELOG.md")
    pending = make_dir(tmp, "rel", PENDING)
    with open(os.path.join(pending, "notes.md"), "w") as fh:
        fh.write("keep me\n")

    out = ok("release", pending, "--changelog", changelog,
             "--version", "1.4.2")
    assert out.strip() == "2.0.0", out
    text = open(changelog).read()
    assert text == "# Changelog\n\n" + RENDERED

    # consumed entries are gone; bystanders stay
    assert sorted(os.listdir(pending)) == ["notes.md"]

    # next release stacks on top, older sections keep their place
    make_dir(tmp, "rel", {"epipe.txt": "fix(cli): handle EPIPE\n"})
    out = ok("release", pending, "--changelog", changelog,
             "--version", "2.0.0")
    assert out.strip() == "2.0.1", out
    text = open(changelog).read()
    assert text.startswith(
        "# Changelog\n\n## 2.0.1\n\n### Fixes\n\n- **cli**: handle EPIPE\n")
    assert text.index("## 2.0.1") < text.index("## 2.0.0")
    assert text.endswith(RENDERED)

    # nothing pending, or nothing that moves the version: refuse, touch nothing
    before = open(changelog).read()
    fail("release", pending, "--changelog", changelog, "--version", "2.0.1")
    make_dir(tmp, "rel", {"deps.txt": "chore: bump deps\n"})
    fail("release", pending, "--changelog", changelog, "--version", "2.0.1")
    assert open(changelog).read() == before
    assert sorted(os.listdir(pending)) == ["deps.txt", "notes.md"]

    # a malformed entry aborts the release before anything is deleted
    make_dir(tmp, "rel", {"good.txt": "feat: shiny\n",
                          "broken.txt": "no header here"})
    err = fail("release", pending, "--changelog", changelog,
               "--version", "2.0.1")
    assert "broken.txt" in err
    assert open(changelog).read() == before
    assert "good.txt" in os.listdir(pending)


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    test_entry_parsing()
    test_semver_math()
    tmp = tempfile.mkdtemp(dir=".")
    try:
        test_bump_and_render(tmp)
        test_release(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
