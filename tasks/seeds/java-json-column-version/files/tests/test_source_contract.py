import re
import unittest
from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "main"
    / "java"
    / "com"
    / "moonshiner"
    / "migration"
    / "JsonColumnMigrator.java"
)


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


def method_body(source, declaration):
    match = re.search(declaration + r"\s*\{", source)
    if match is None:
        raise AssertionError(f"missing Java method matching {declaration!r}")
    return braced_body(source, match.end() - 1)


def without_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def compact(source):
    return re.sub(r"\s+", "", without_comments(source))


def if_body(source, condition):
    match = re.search(r"if\s*\(" + condition + r"\)\s*\{", source)
    if match is None:
        raise AssertionError(f"missing Java branch matching {condition!r}")
    return braced_body(source, match.end() - 1)


class JsonColumnMigratorSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.migrate = method_body(
            source,
            r"public\s+MigrationReport\s+migrate\(int\s+checkpointEvery\)",
        )

    def test_full_chunks_are_committed_as_transaction_boundaries(self):
        checkpoint = compact(
            if_body(self.migrate, r"sinceCheckpoint\s*==\s*checkpointEvery")
        )
        save = "transaction.saveCheckpoint(row.id());"
        commit = "transaction.commit();"
        reopen = "transaction=store.beginTransaction();"
        self.assertIn(save, checkpoint)
        self.assertIn(commit, checkpoint)
        self.assertIn(reopen, checkpoint)
        self.assertLess(checkpoint.index(save), checkpoint.index(commit))
        self.assertLess(checkpoint.index(commit), checkpoint.index(reopen))

    def test_final_partial_chunk_is_committed(self):
        migrate = compact(self.migrate)
        partial = "if(sinceCheckpoint>0)"
        save = "transaction.saveCheckpoint(lastRowId);"
        commit = "transaction.commit();"
        self.assertIn(partial, migrate)
        partial_offset = migrate.rindex(partial)
        save_offset = migrate.index(save, partial_offset)
        commit_offset = migrate.index(commit, save_offset)
        self.assertLess(
            save_offset,
            commit_offset,
        )

    def test_failure_rolls_back_the_current_chunk_and_rethrows(self):
        catch = re.search(
            r"catch\s*\(\s*RuntimeException\s+failure\s*\)\s*\{",
            without_comments(self.migrate),
        )
        self.assertIsNotNone(catch, "missing migration failure handler")
        body = compact(braced_body(without_comments(self.migrate), catch.end() - 1))
        self.assertIn("transaction.rollback();", body)
        self.assertIn("throwfailure;", body)

    def test_document_migration_and_malformed_row_isolation_are_preserved(self):
        migrate = compact(self.migrate)
        self.assertIn("intoriginalVersion=schemaVersion(document);", migrate)
        self.assertIn("migrateDocument(document,originalVersion);", migrate)
        self.assertIn("if(originalVersion!=CURRENT_VERSION)", migrate)
        self.assertIn("transaction.replaceJson(row.id(),codec.writeObject(document));", migrate)
        self.assertIn(
            "catch(InvalidDocumentException|IllegalArgumentExceptionmalformed)",
            migrate,
        )
        self.assertIn("transaction.quarantine(row.id(),malformed.getMessage());", migrate)

        source = SOURCE_PATH.read_text(encoding="utf-8")
        migration = compact(
            method_body(
                source,
                r"private\s+void\s+migrateDocument"
                r"\(Map<String,\s*Object>\s+document,\s*int\s+version\)",
            )
        )
        self.assertIn(
            'renameIfPresent(document,"display_name","displayName");', migration
        )
        self.assertIn(
            'renameIfPresent(document,"contact_email","email");', migration
        )
        self.assertIn(
            'document.put("_schemaVersion",BigDecimal.valueOf(nextVersion));',
            migration,
        )
        self.assertNotIn("document.clear();", migration)


if __name__ == "__main__":
    unittest.main()
