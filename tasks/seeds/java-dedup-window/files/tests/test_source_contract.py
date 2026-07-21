import re
import unittest
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / "EventPosition.java"


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


class EventPositionSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")

    def test_equality_uses_partition_and_sequence_as_composite_identity(self):
        equals = compact(
            method_body(
                self.source,
                r"public\s+boolean\s+equals\(Object\s+other\)",
            )
        )
        self.assertIn("sequence==that.sequence", equals)
        self.assertTrue(
            "partition.equals(that.partition)" in equals
            or "that.partition.equals(partition)" in equals
            or "Objects.equals(partition,that.partition)" in equals,
            "EventPosition equality must include the partition",
        )

        composite_return = re.search(
            r"return(?:sequence==that\.sequence&&"
            r"(?:partition\.equals\(that\.partition\)|"
            r"that\.partition\.equals\(partition\)|"
            r"Objects\.equals\(partition,that\.partition\))|"
            r"(?:partition\.equals\(that\.partition\)|"
            r"that\.partition\.equals\(partition\)|"
            r"Objects\.equals\(partition,that\.partition\))"
            r"&&sequence==that\.sequence);",
            equals,
        )
        self.assertIsNotNone(
            composite_return,
            "partition and sequence must jointly decide value equality",
        )

    def test_hash_code_uses_the_same_composite_identity(self):
        hash_code = compact(
            method_body(self.source, r"public\s+int\s+hashCode\(\)")
        )
        supported = (
            re.search(
                r"partition\.hashCode\(\).*Long\.hashCode\(sequence\)",
                hash_code,
            )
            or re.search(
                r"Long\.hashCode\(sequence\).*partition\.hashCode\(\)",
                hash_code,
            )
            or "returnObjects.hash(partition,sequence);" in hash_code
        )
        self.assertTrue(
            supported,
            "equal EventPosition values need a partition-and-sequence hash",
        )


if __name__ == "__main__":
    unittest.main()
