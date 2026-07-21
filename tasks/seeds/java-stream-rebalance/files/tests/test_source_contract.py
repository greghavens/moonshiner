import re
import unittest
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / "StreamProcessor.java"


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


class StreamRebalanceSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.revoked = without_comments(method_body(
            cls.source,
            r"public\s+void\s+onPartitionsRevoked"
            r"\(Set<TopicPartition>\s+partitions\)",
        ))

    def test_revoked_progress_is_committed_before_local_ownership_is_dropped(self):
        calls = list(re.finditer(r"commitPartitions\s*\((.*?)\)\s*;", self.revoked))
        self.assertEqual(1, len(calls),
                         "revocation must make one partition-scoped handoff commit")
        argument = re.sub(r"\s+", "", calls[0].group(1))

        derived_from_revocations = argument == "partitions"
        if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", argument):
            declaration = self.revoked[:calls[0].start()]
            derived_from_revocations = re.search(
                r"\b" + re.escape(argument)
                + r"\s*=\s*new\s+TreeSet\s*<>\s*\(\s*partitions\s*\)\s*;",
                declaration,
            ) is not None
        self.assertTrue(
            derived_from_revocations,
            "the handoff commit must be limited to the partitions being revoked",
        )

        ownership_drop = re.search(r"assigned\s*\.\s*remove(?:All)?\s*\(", self.revoked)
        self.assertIsNotNone(ownership_drop, "revoked local ownership must be removed")
        self.assertLess(
            calls[0].start(), ownership_drop.start(),
            "the handoff commit must run before local ownership is removed",
        )

    def test_revocation_clears_only_revoked_local_state(self):
        body = compact(self.revoked)
        self.assertNotIn("commitPending()", body,
                         "revocation must not flush retained partitions")
        self.assertNotIn("pending.clear()", body,
                         "revocation must not clear retained pending progress")
        self.assertNotIn("assigned.clear()", body,
                         "a partial rebalance must preserve retained ownership")
        self.assertNotIn("nextOffsets.clear()", body,
                         "a partial rebalance must preserve retained positions")
        self.assertIn("assigned.remove(partition);", body)
        self.assertIn("nextOffsets.remove(partition);", body)

        helper = compact(method_body(
            self.source,
            r"private\s+void\s+commitPartitions"
            r"\(Collection<TopicPartition>\s+partitions\)",
        ))
        commit = "checkpoints.commit(memberId,partition,nextOffset);"
        clear = "pending.remove(partition,nextOffset);"
        self.assertIn(commit, helper)
        self.assertIn(clear, helper)
        self.assertLess(helper.index(commit), helper.index(clear))

    def test_assignment_resumes_from_the_stored_next_offset(self):
        assigned = compact(method_body(
            self.source,
            r"public\s+void\s+onPartitionsAssigned"
            r"\(Set<TopicPartition>\s+partitions\)",
        ))
        process = compact(method_body(
            self.source,
            r"public\s+void\s+process\(List<StreamRecord>\s+records\)",
        ))
        self.assertIn(
            "nextOffsets.put(partition,checkpoints.committed(partition));",
            assigned,
        )
        self.assertIn("if(record.offset()<expected){continue;}", process)


if __name__ == "__main__":
    unittest.main()
