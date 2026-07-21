import re
import unittest
from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "main"
    / "java"
    / "incident"
    / "FundsTransferService.java"
)


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


def method_body(source, declaration):
    match = re.search(declaration + r"\s*\{", source)
    if match is None:
        raise AssertionError(f"missing Java method matching {declaration!r}")
    return braced_body(source, match.end() - 1)


def compact(source):
    return re.sub(r"\s+", "", without_comments(source))


def first_database_catch(source):
    uncommented = without_comments(source)
    match = re.search(
        r"catch\s*\(\s*DatabaseException\s+(\w+)\s*\)\s*\{",
        uncommented,
    )
    if match is None:
        raise AssertionError("missing DatabaseException failure handler")
    return match.group(1), braced_body(uncommented, match.end() - 1)


class FundsTransferServiceSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.transfer = method_body(
            cls.source,
            r"public\s+void\s+transfer"
            r"\(String\s+requestId,\s*Transfer\s+transfer\)"
            r"\s+throws\s+DatabaseException",
        )
        cls.normalized = compact(cls.transfer)

    def test_validation_still_precedes_the_pool_checkout(self):
        required = [
            'Objects.requireNonNull(requestId,"requestId");',
            'if(requestId.isBlank()){thrownewIllegalArgumentException('
            '"requestIdmustnotbeblank");}',
            'Objects.requireNonNull(transfer,"transfer");',
            "pool.borrow(requestId)",
        ]
        offsets = []
        for statement in required:
            self.assertIn(statement, self.normalized)
            offsets.append(self.normalized.index(statement))
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(1, self.normalized.count("pool.borrow(requestId)"))

    def test_transaction_steps_and_failure_causality_are_preserved(self):
        ordered = [
            "connection.begin();",
            "connection.debit(transfer.debitAccount(),transfer.cents());",
            "connection.credit(transfer.creditAccount(),transfer.cents());",
            "connection.commit();",
        ]
        offsets = []
        for statement in ordered:
            self.assertIn(statement, self.normalized)
            offsets.append(self.normalized.index(statement))
        self.assertEqual(offsets, sorted(offsets))

        operation_name, operation_body = first_database_catch(self.transfer)
        operation_catch = compact(operation_body)
        rollback = "connection.rollback();"
        self.assertIn(rollback, operation_catch)

        rollback_catch = re.search(
            r"catch\(DatabaseException(\w+)\)\{",
            operation_catch,
        )
        self.assertIsNotNone(rollback_catch, "rollback cleanup failure is lost")
        rollback_name = rollback_catch.group(1)
        suppress = operation_name + ".addSuppressed(" + rollback_name + ");"
        rethrow = "throw" + operation_name + ";"
        self.assertIn(suppress, operation_catch)
        self.assertIn(rethrow, operation_catch)
        self.assertLess(operation_catch.index(rollback), operation_catch.index(rethrow))
        self.assertLess(operation_catch.index(suppress), operation_catch.index(rethrow))

    def test_borrowed_connection_has_exception_safe_ownership_release(self):
        resource = re.search(
            r"try\((?:final)?(?:DatabaseConnection|var)connection="
            r"pool\.borrow\(requestId\)\)\{",
            self.normalized,
        )
        reusable_resource = "try(connection){" in self.normalized
        if resource is not None or reusable_resource:
            self.assertNotIn("connection.close();", self.normalized)
            return

        self.assertIn(
            "DatabaseConnectionconnection=pool.borrow(requestId);",
            self.normalized,
        )

        operation_name, operation_body = first_database_catch(self.transfer)
        operation_catch = compact(operation_body)
        close_and_suppress = re.compile(
            r"try\{connection\.close\(\);\}"
            r"catch\(DatabaseException(\w+)\)\{"
            + re.escape(operation_name)
            + r"\.addSuppressed\(\1\);\}"
        )

        # An explicit implementation may release on the two exclusive paths:
        # suppressing close failure on the operation path and reporting close
        # directly after successful commit.
        if close_and_suppress.search(operation_catch):
            self.assertEqual(2, self.normalized.count("connection.close();"))
            return

        # Or it may track the primary failure and perform one close from an
        # outer finally, suppressing a close failure onto that tracked primary.
        finally_match = re.search(r"finally\{", self.normalized)
        self.assertIsNotNone(finally_match, "connection release is not exception-safe")
        finally_body = braced_body(self.normalized, finally_match.end() - 1)
        self.assertEqual(1, finally_body.count("connection.close();"))
        close_catch = re.search(
            r"try\{connection\.close\(\);\}"
            r"catch\(DatabaseException(\w+)\)\{"
            r"(\w+)\.addSuppressed\(\1\);\}",
            finally_body,
        )
        self.assertIsNotNone(
            close_catch,
            "a close failure could replace the original operation failure",
        )
        primary_name = close_catch.group(2)
        self.assertIn(primary_name + "=" + operation_name + ";", self.normalized)


if __name__ == "__main__":
    unittest.main()
