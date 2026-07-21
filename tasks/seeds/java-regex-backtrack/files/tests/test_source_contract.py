import re
import unittest
from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "main"
    / "java"
    / "moonshiner"
    / "routes"
    / "RouteSpecParser.java"
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


class RouteSpecParserSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.parse_measured_source = without_comments(
            method_body(
                cls.source,
                r"public\s+static\s+ParseResult\s+parseMeasured"
                r"\(String\s+input,\s*long\s+operationLimit\)",
            )
        )
        cls.parse_measured = compact(cls.parse_measured_source)

    def test_regex_apis_and_secondary_string_scans_are_absent(self):
        source = without_comments(self.source)
        forbidden = (
            "java.util.regex",
            "Pattern.compile",
            ".matcher(",
            ".matches(",
            ".split(",
            ".toCharArray(",
            ".chars(",
            ".codePoints(",
            ".indexOf(",
            ".lastIndexOf(",
            "StringTokenizer",
            "Scanner",
        )
        for fragment in forbidden:
            self.assertNotIn(fragment, source)

    def test_parser_is_a_single_instrumented_forward_pass(self):
        parse = self.parse_measured
        self.assertIn('Objects.requireNonNull(input,"input");', parse)
        self.assertRegex(parse, r"if\(operationLimit(?:<1|<=0)\)")

        measured_declaration = re.search(
            r"CountingCharSequence(\w+)=newCountingCharSequence"
            r"\(input,operationLimit\);",
            parse,
        )
        self.assertIsNotNone(
            measured_declaration,
            "parseMeasured must wrap its input in CountingCharSequence",
        )
        measured = measured_declaration.group(1)

        inspections = re.findall(r"\b(\w+)\.charAt\((\w+)\)", parse)
        self.assertEqual(
            1,
            len(inspections),
            "the parser must have exactly one instrumented charAt call site",
        )
        self.assertEqual(
            measured,
            inspections[0][0],
            "the parser must inspect the measured sequence",
        )
        self.assertNotIn("input.charAt(", parse)

        inspection = f"{measured}.charAt("
        loop_bodies = []
        for loop in re.finditer(
            r"\b(?:for|while)\s*\([^{}]*\)\s*\{",
            self.parse_measured_source,
        ):
            loop_bodies.append(braced_body(self.parse_measured_source, loop.end() - 1))
        self.assertTrue(
            any(inspection in body for body in loop_bodies),
            "the instrumented inspection must occur in a forward parser loop",
        )

        for literal in ("'a'", "'z'", "'.'"):
            self.assertIn(literal, parse, f"missing grammar check for {literal}")
        self.assertIn(".substring(", parse)
        self.assertIn(".add(", parse)
        self.assertGreaterEqual(parse.count("newSyntaxException("), 2)
        self.assertIn(f"{measured}.operationCount()", parse)
        self.assertTrue(
            "Collections.unmodifiableList(" in parse or "List.copyOf(" in parse,
            "returned segments must be unmodifiable",
        )

    def test_operation_limit_instrumentation_remains_truthful(self):
        char_at = compact(
            method_body(
                self.source,
                r"public\s+char\s+charAt\(int\s+index\)",
            )
        )
        required_in_order = (
            "operationCount++;",
            "if(operationCount>operationLimit)",
            "thrownewOperationLimitExceededException(operationLimit);",
            "returnvalue.charAt(index);",
        )
        offset = 0
        for fragment in required_in_order:
            next_offset = char_at.find(fragment, offset)
            self.assertNotEqual(-1, next_offset, f"missing counter step: {fragment}")
            offset = next_offset + len(fragment)
        self.assertEqual(1, char_at.count("operationCount++"))


if __name__ == "__main__":
    unittest.main()
