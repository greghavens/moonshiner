import re
import unittest
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / "BundleStreamer.java"


def without_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def braced_body(source, opening_brace):
    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening_brace + 1:index]
    raise AssertionError("unterminated Java block")


def declared_body(source, declaration):
    match = re.search(declaration + r"\s*\{", source)
    if match is None:
        raise AssertionError(f"missing Java declaration matching {declaration!r}")
    return braced_body(source, match.end() - 1)


def compact(source):
    return re.sub(r"\s+", "", without_comments(source))


class BundleStreamerSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.open_body = declared_body(
            source,
            r"public\s+InputStream\s+open\s*\(\s*Path\s+stagedArchive\s*,"
            r"\s*String\s+entryName\s*\)\s+throws\s+IOException",
        )
        managed = declared_body(
            source,
            r"private\s+static\s+final\s+class\s+ManagedEntryStream"
            r"\s+extends\s+FilterInputStream",
        )
        cls.managed_body = managed
        cls.close_body = declared_body(
            managed,
            r"public\s+void\s+close\s*\(\s*\)\s+throws\s+IOException",
        )

    def test_open_returns_the_entry_stream_without_reading_or_buffering_it(self):
        opened = compact(self.open_body)
        self.assertIn("InputStreamentry=owner.openEntry(entryName);", opened)
        self.assertIn(
            "returnnewManagedEntryStream(entry,owner,stagedArchive,lockTracker);",
            opened,
        )
        self.assertNotRegex(opened, r"read(?:AllBytes|NBytes)?\(")
        self.assertNotIn("ByteArrayInputStream", opened)

    def test_explicit_close_is_idempotent(self):
        closed = compact(self.close_body)
        guard = "if(closed){return;}"
        mark = "closed=true;"
        self.assertIn(guard, closed)
        self.assertIn(mark, closed)
        self.assertLess(closed.index(guard), closed.index(mark))
        first_cleanup = min(
            closed.index("attempt(in::close"),
            closed.index("lockTracker.delete(stagedArchive)"),
        )
        self.assertLess(closed.index(mark), first_cleanup)

    def test_close_releases_entry_then_owner_then_deletes_stage(self):
        closed = compact(self.close_body)
        entry = "failure=attempt(in::close,failure);"
        owner = "failure=attempt(owner::close,failure);"
        delete = (
            "failure=attempt(()->lockTracker.delete(stagedArchive),failure);"
        )
        for step in (entry, owner, delete):
            self.assertEqual(
                1,
                closed.count(step),
                f"cleanup must contain exactly one {step}",
            )
        self.assertLess(closed.index(entry), closed.index(owner))
        self.assertLess(closed.index(owner), closed.index(delete))
        self.assertIn("if(failure!=null){rethrow(failure);}", closed)

    def test_read_paths_do_not_implicitly_close_the_owner(self):
        managed = compact(self.managed_body)
        self.assertNotRegex(managed, r"(?:int|byte\[\])read(?:NBytes|AllBytes)?\(")

    def test_setup_failure_keeps_primary_error_and_attempts_all_cleanup(self):
        opened = compact(self.open_body)
        catch = re.search(
            r"catch\(ThrowableopenFailure\)\{(?P<body>.*?)\}", opened
        )
        self.assertIsNotNone(catch, "missing setup-failure cleanup")
        body = catch.group("body")
        owner = "Throwablefailure=attempt(owner::close,openFailure);"
        delete = (
            "failure=attempt(()->lockTracker.delete(stagedArchive),failure);"
        )
        rethrow = "rethrow(failure);"
        for step in (owner, delete, rethrow):
            self.assertIn(step, body)
        self.assertLess(body.index(owner), body.index(delete))
        self.assertLess(body.index(delete), body.index(rethrow))

    def test_attempt_helper_preserves_primary_and_suppresses_later_errors(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")
        attempt = compact(
            declared_body(
                source,
                r"private\s+static\s+Throwable\s+attempt\s*\("
                r"\s*CleanupAction\s+action\s*,\s*Throwable\s+failure\s*\)",
            )
        )
        self.assertIn("action.run();", attempt)
        self.assertIn("if(failure==null){returncleanupFailure;}", attempt)
        self.assertIn(
            "if(failure!=cleanupFailure){failure.addSuppressed(cleanupFailure);}",
            attempt,
        )
        self.assertTrue(attempt.endswith("returnfailure;"))


if __name__ == "__main__":
    unittest.main()
