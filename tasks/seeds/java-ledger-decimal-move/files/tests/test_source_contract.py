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
    / "ledger"
    / "LedgerAmountMigration.java"
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


class LedgerAmountSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")

    def test_version_dispatch_preserves_decimal_rows(self):
        migrate = method_body(
            self.source,
            r"public\s+static\s+PersistedAmount\s+migrate\(PersistedAmount\s+stored\)",
        )
        branch = re.search(
            r"if\s*\(stored\.getSchemaVersion\(\)\s*==\s*DECIMAL_VERSION\)\s*\{",
            migrate,
        )
        self.assertIsNotNone(branch, "missing version 2 migration branch")
        decimal_branch = braced_body(migrate, branch.end() - 1)
        normalized_branch = compact(decimal_branch)
        self.assertIn(
            normalized_branch,
            {
                "validateDecimal(stored.getAmount());returnstored;",
                "validateDecimal(stored.getAmount());"
                "returnnewPersistedAmount(DECIMAL_VERSION,stored.getAmount());",
            },
            "version 2 amount is not validated and returned unchanged",
        )

        self.assertRegex(
            migrate,
            r"MINOR_UNITS_VERSION[\s\S]*parseMinorUnits\s*\(stored\.getAmount\(\)\)"
            r"[\s\S]*new\s+PersistedAmount\s*\(DECIMAL_VERSION,\s*fromMinorUnits",
        )
        self.assertRegex(migrate, r"Unsupported ledger amount schema version")

    def test_negative_zero_is_explicitly_noncanonical(self):
        parse_minor = method_body(
            self.source,
            r"private\s+static\s+long\s+parseMinorUnits\(String\s+value\)",
        )
        validate_decimal = method_body(
            self.source,
            r"private\s+static\s+void\s+validateDecimal\(String\s+value\)",
        )
        minor_guard = re.search(
            r"if\s*\((.*?)\)\s*\{\s*throw\s+new\s+IllegalArgumentException",
            without_comments(parse_minor),
            re.DOTALL,
        )
        decimal_guard = re.search(
            r"if\s*\((.*?)\)\s*\{\s*throw\s+new\s+IllegalArgumentException",
            without_comments(validate_decimal),
            re.DOTALL,
        )
        self.assertIsNotNone(minor_guard, "missing minor-unit validation guard")
        self.assertIsNotNone(decimal_guard, "missing decimal validation guard")
        self.assertIn('"-0".equals(value)', compact(minor_guard.group(1)))
        self.assertIn('"-0.00".equals(value)', compact(decimal_guard.group(1)))
        self.assertIn("longValueExact()", parse_minor)
        self.assertIn("longValueExact()", validate_decimal)

    def test_public_mapping_remains_exact(self):
        from_minor = method_body(
            self.source,
            r"public\s+static\s+String\s+fromMinorUnits\(long\s+minorUnits\)",
        )
        to_minor = method_body(
            self.source,
            r"public\s+static\s+long\s+toMinorUnits\(String\s+decimalAmount\)",
        )
        self.assertRegex(
            from_minor,
            r"BigDecimal\.valueOf\s*\(minorUnits,\s*2\)\.toPlainString\(\)",
        )
        self.assertRegex(to_minor, r"validateDecimal\s*\(decimalAmount\)\s*;")
        self.assertRegex(
            to_minor,
            r"new\s+BigDecimal\s*\(decimalAmount\)\.movePointRight\s*\(2\)"
            r"\.longValueExact\s*\(\)",
        )


if __name__ == "__main__":
    unittest.main()
