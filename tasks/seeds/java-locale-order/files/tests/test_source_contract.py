import re
import unittest
from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "testFixtures"
    / "java"
    / "com"
    / "moonshiner"
    / "testing"
    / "ScopedDefaults.java"
)


def without_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def compact(source):
    return re.sub(r"\s+", "", without_comments(source))


def braced_span(source, opening_brace):
    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening_brace + 1:index], index
    raise AssertionError("unterminated Java block")


def method_body(source):
    declaration = (
        r"public\s+static\s+void\s+runWith\s*\(\s*"
        r"Locale\s+locale\s*,\s*TimeZone\s+timeZone\s*,\s*"
        r"CheckedRunnable\s+action\s*\)\s*throws\s+Exception\s*\{"
    )
    match = re.search(declaration, source)
    if match is None:
        raise AssertionError("ScopedDefaults.runWith API changed")
    return braced_span(source, match.end() - 1)[0]


def captured_name(prefix, declaration, label):
    matches = re.findall(declaration, prefix)
    if len(matches) != 1:
        raise AssertionError(f"expected one entry-state capture for {label}")
    return matches[0]


class ScopedDefaultsSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = method_body(SOURCE_PATH.read_text(encoding="utf-8"))

    def test_scope_uses_exact_entry_state_and_finally(self):
        source = without_comments(self.body)
        try_match = re.search(r"\btry\s*\{", source)
        self.assertIsNotNone(try_match, "runWith must use try/finally")
        prefix = source[:try_match.start()]

        previous_locale = captured_name(
            prefix,
            r"Locale\s+([A-Za-z_$][\w$]*)\s*=\s*"
            r"Locale\.getDefault\s*\(\s*\)\s*;",
            "the general locale",
        )
        previous_display_locale = captured_name(
            prefix,
            r"Locale\s+([A-Za-z_$][\w$]*)\s*=\s*Locale\.getDefault\s*"
            r"\(\s*Locale\.Category\.DISPLAY\s*\)\s*;",
            "the display locale",
        )
        previous_format_locale = captured_name(
            prefix,
            r"Locale\s+([A-Za-z_$][\w$]*)\s*=\s*Locale\.getDefault\s*"
            r"\(\s*Locale\.Category\.FORMAT\s*\)\s*;",
            "the format locale",
        )
        previous_time_zone = captured_name(
            prefix,
            r"TimeZone\s+([A-Za-z_$][\w$]*)\s*=\s*"
            r"TimeZone\.getDefault\s*\(\s*\)\s*;",
            "the time zone",
        )

        try_body, try_end = braced_span(source, try_match.end() - 1)
        self.assertEqual(
            "Locale.setDefault(locale);"
            "TimeZone.setDefault(timeZone);"
            "action.run();",
            compact(try_body),
            "scope must install both requested defaults and invoke the callback once",
        )

        finally_match = re.match(r"\s*finally\s*\{", source[try_end + 1:])
        self.assertIsNotNone(
            finally_match,
            "callback failures must flow directly through a finally block",
        )
        finally_open = try_end + 1 + finally_match.end() - 1
        finally_body, finally_end = braced_span(source, finally_open)
        expected_finally = (
            f"Locale.setDefault({previous_locale});"
            "Locale.setDefault(Locale.Category.DISPLAY,"
            f"{previous_display_locale});"
            "Locale.setDefault(Locale.Category.FORMAT,"
            f"{previous_format_locale});"
            f"TimeZone.setDefault({previous_time_zone});"
        )
        self.assertEqual(
            expected_finally,
            compact(finally_body),
            "finally must restore every exact entry default",
        )
        self.assertEqual(
            "",
            compact(source[finally_end + 1:]),
            "runWith must not replace the callback result after restoration",
        )


if __name__ == "__main__":
    unittest.main()
