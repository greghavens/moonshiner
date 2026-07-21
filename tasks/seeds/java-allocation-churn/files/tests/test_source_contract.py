import re
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
PARSER_PATH = (
    PROJECT_DIR
    / "src"
    / "main"
    / "java"
    / "com"
    / "moonshiner"
    / "telemetry"
    / "TelemetryParser.java"
)
COUNTER_PATH = PARSER_PATH.with_name("ParserAllocationCounter.java")
MAIN_SOURCE_DIR = PROJECT_DIR / "src" / "main" / "java"


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


def direct_member_statements(class_body):
    statements = []
    depth = 0
    start = 0
    for index, character in enumerate(class_body):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                start = index + 1
        elif character == ";" and depth == 0:
            statements.append(class_body[start:index + 1].strip())
            start = index + 1
    return statements


class TelemetryParserSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PARSER_PATH.read_text(encoding="utf-8")
        cls.parse = method_body(
            cls.source,
            r"public\s+List<TelemetryRecord>\s+parse\(String\s+input\)",
        )

    def test_one_guarded_scratch_allocation_is_reused_per_invocation(self):
        parse = compact(self.parse)
        declaration = re.search(r"RecordScratchscratch(?:=[^;]+)?;", parse)
        loop = "while(offset<input.length())"

        self.assertIsNotNone(declaration, "missing invocation-local scratch variable")
        self.assertIn(loop, parse)
        loop_offset = parse.index(loop)
        self.assertLess(declaration.start(), loop_offset)
        self.assertEqual(1, parse.count("newRecordScratch("))
        allocation_offset = parse.index("newRecordScratch(")
        if allocation_offset > loop_offset:
            self.assertIn(
                "if(scratch==null){scratch=",
                parse[loop_offset:allocation_offset],
                "a loop-local allocation must be guarded so it runs once",
            )
        else:
            before_allocation = parse[:allocation_offset]
            has_empty_guard = (
                "if(input.isEmpty())" in before_allocation
                or "if(input.length()==0)" in before_allocation
                or "if(0==input.length())" in before_allocation
            )
            self.assertTrue(
                has_empty_guard,
                "an eager allocation must be preceded by an empty-input guard",
            )
        self.assertIn("scratch.reset();", parse[loop_offset:])
        self.assertIn(
            "records.add(parseRecord(input,offset,recordEnd,line,scratch));",
            parse,
        )

    def test_parse_control_flow_and_result_contract_are_preserved(self):
        parse = compact(self.parse)
        required_fragments = (
            'Objects.requireNonNull(input,"input");',
            "List<TelemetryRecord>records=newArrayList<>();",
            "intoffset=0;intline=1;",
            "while(offset<input.length())",
            "intrecordEnd=findRecordEnd(input,offset,line);",
            'if(recordEnd==offset){throwerror(line,1,"emptyrecord");}',
            "if(recordEnd==input.length()){offset=recordEnd;}",
            "elseif(input.charAt(recordEnd)=='\\r'){offset=recordEnd+2;}",
            "else{offset=recordEnd+1;}line++;",
            "returnList.copyOf(records);",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, parse)

    def test_reset_reuses_the_array_without_exposing_stale_characters(self):
        reset = compact(
            method_body(
                self.source,
                r"private\s+void\s+reset\(\)",
            )
        )
        string_from = compact(
            method_body(
                self.source,
                r"private\s+String\s+stringFrom\(int\s+start\)",
            )
        )
        self.assertRegex(reset, r"(?:this\.)?length=0;")
        self.assertNotIn("newchar[", reset)
        self.assertNotIn("scratchAllocated", reset)
        self.assertIn("newString(characters,start,length-start)", string_from)

    def test_workspace_allocation_instrumentation_remains_truthful(self):
        constructor = compact(
            method_body(
                self.source,
                r"private\s+RecordScratch"
                r"\(int\s+capacity,\s*ParserAllocationCounter\s+allocations\)",
            )
        )
        counted = "allocations.scratchAllocated(capacity);"
        allocated = "characters=newchar[capacity];"
        self.assertIn(counted, constructor)
        self.assertIn(allocated, constructor)
        self.assertLess(constructor.index(counted), constructor.index(allocated))
        self.assertEqual(1, compact(self.source).count("newRecordScratch("))
        self.assertEqual(1, compact(self.source).count("newchar["))
        self.assertNotIn("Arrays.copyOf", self.source)
        self.assertNotIn("toCharArray", self.source)

        counter_source = COUNTER_PATH.read_text(encoding="utf-8")
        counter_method = compact(
            method_body(
                counter_source,
                r"void\s+scratchAllocated\(int\s+capacity\)",
            )
        )
        self.assertEqual(
            "scratchInstances++;scratchArrays++;"
            "scratchCharacterCapacity+=capacity;",
            counter_method,
        )

    def test_no_static_or_parser_instance_scratch_holder_is_introduced(self):
        all_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(MAIN_SOURCE_DIR.rglob("*.java"))
        )
        source = without_comments(self.source)
        uncommented_all_source = without_comments(all_source)
        self.assertNotIn("ThreadLocal", uncommented_all_source)
        self.assertIsNone(
            re.search(
                r"\bstatic\b[^;{}()]*\b"
                r"(?:RecordScratch|[\w$.<>?]+\s*\[\]|StringBuilder|StringBuffer|"
                r"CharBuffer)"
                r"\s+\w+\s*(?:=|;)",
                uncommented_all_source,
            ),
            "scratch storage must not be held in a static field",
        )
        self.assertIsNone(
            re.search(r"\bstatic\b[^;{}()]*new\s+char\s*\[", uncommented_all_source),
            "a static object holder must not hide a scratch array",
        )
        self.assertIsNone(
            re.search(
                r"\bstatic\b[^;{}()]*\b\w*(?:scratch|buffer|cache)\w*"
                r"\s*(?:=|;)",
                uncommented_all_source,
                flags=re.IGNORECASE,
            ),
            "scratch storage must not be hidden behind a static holder",
        )

        class_open = re.search(r"public\s+final\s+class\s+TelemetryParser\s*\{", source)
        self.assertIsNotNone(class_open)
        class_body = braced_body(source, class_open.end() - 1)
        for statement in direct_member_statements(class_body):
            normalized = compact(statement)
            if "static" in normalized:
                continue
            self.assertIsNone(
                re.search(
                    r"(?:RecordScratch|[\w$.<>?]+\[\]|StringBuilder|"
                    r"StringBuffer|CharBuffer|ThreadLocal)",
                    normalized,
                ),
                "TelemetryParser must not retain scratch state between invocations",
            )
            self.assertNotRegex(
                normalized.lower(),
                r"\b\w*(?:scratch|buffer|cache)\w*\s*(?:=|;)",
                "TelemetryParser must not retain a disguised scratch holder",
            )

    def test_diagnostic_paths_remain_present(self):
        source = without_comments(self.source)
        reasons = {
            "empty record",
            "bare carriage return",
            "expected '|' after sequence",
            "sequence must not be empty",
            "sequence must be an unsigned integer",
            "sequence is out of range",
            "expected '|' after key",
            "key must not be empty",
            "unexpected fourth field",
            "trailing escape",
        }
        for reason in reasons:
            self.assertIn(f'"{reason}"', source)
        self.assertIn('"invalid escape \'\\\\" + escaped + "\'"', source)
        self.assertIn(
            "return new TelemetryParseException(line, column, reason);",
            source,
        )


if __name__ == "__main__":
    unittest.main()
