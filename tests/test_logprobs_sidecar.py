"""The columnar sidecar that carries a teacher's token distributions.

Every failure this format can have is silent. A sidecar that stores decoded
text instead of ids, renormalizes its truncated head, or files a turn's tokens
against the wrong turn still loads, still trains, and produces a loss curve
that looks exactly like a correct one. So the guarantees are asserted here
rather than trusted: ids stay integers, the captured mass stays partial, and
every row lands on the assistant turn that generated it.
"""
from __future__ import annotations

import json
import math
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from logprobs_sidecar import (  # noqa: E402
    ASSISTANT_SEGMENT,
    GeneratedToken,
    LogprobsUnavailable,
    TurnLogprobs,
    alignment_report,
    assistant_turn_count,
    manifest_entry,
    read_sidecar,
    summary,
    write_manifest,
    write_sidecar,
)

TOP_K = 4

# One assistant turn's worth of a plausible truncated head: the sampled token
# is the likeliest, and the four captured probabilities deliberately fall well
# short of summing to one.
HEAD = [-0.30, -2.10, -3.60, -5.20]


def _token(token_id: int, *, alternatives: int = TOP_K) -> GeneratedToken:
    return GeneratedToken(
        token_id=token_id,
        logprob=HEAD[0],
        top_token_ids=[token_id + 1000 * rank for rank in range(alternatives)],
        top_logprobs=HEAD[:alternatives],
    )


def _turn(index: int, token_ids: list[int], **facts) -> TurnLogprobs:
    return TurnLogprobs(assistant_turn_index=index,
                        tokens=[_token(value) for value in token_ids], **facts)


# A canonical trace shaped like one this backend produces: a tool-calling turn,
# its result, a reply, an operator follow-up, and a final reply.
TRACE = [
    {"role": "user", "content": "Fix the failing test."},
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "call_1", "type": "function",
                     "function": {"name": "bash", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "1 failed"},
    {"role": "assistant", "content": "Fixed the off-by-one."},
    {"role": "user", "content": "Now add a regression test."},
    {"role": "assistant", "content": "Added it."},
]
TURNS = [_turn(1, [11, 12, 13]), _turn(2, [21, 22]), _turn(3, [31, 32, 33, 34])]


class Storage(unittest.TestCase):
    def _write(self, turns=None, **options) -> tuple[pathlib.Path, dict]:
        directory = pathlib.Path(tempfile.mkdtemp(prefix="moonshiner-logprobs-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = directory / "trajectory.parquet"
        facts = write_sidecar(path, trajectory_id="seed-1",
                              turns=TURNS if turns is None else turns,
                              top_k=TOP_K, **options)
        return path, facts

    def test_a_token_survives_the_round_trip_as_an_integer_id(self):
        # The loader must never have to re-tokenize: a tokenizer that
        # disagrees with the server by one token shifts every target after it
        # against the wrong distribution, and nothing reports that.
        path, _ = self._write()
        table, _ = read_sidecar(path)
        self.assertEqual(table.schema.field("token_id").type, pa.int32())
        self.assertEqual(table.schema.field("top_token_ids").type,
                         pa.list_(pa.int32()))
        self.assertEqual(table.column("token_id").to_pylist()[:3], [11, 12, 13])
        for value in table.column("top_token_ids").to_pylist()[0]:
            self.assertIsInstance(value, int)

    def test_the_head_is_stored_exactly_as_the_server_reported_it(self):
        # Renormalizing would make the captured probabilities sum to one and
        # throw away how much belief lies outside the K -- which is the term a
        # KL objective needs in order to be honest about truncation.
        path, _ = self._write()
        table, metadata = read_sidecar(path)
        captured = table.column("top_logprobs").to_pylist()[0]
        self.assertEqual(len(captured), TOP_K)
        for stored, reported in zip(captured, HEAD):
            self.assertAlmostEqual(stored, reported, places=5)
        self.assertLess(sum(math.exp(value) for value in captured), 1.0)
        self.assertEqual(metadata["renormalized"], "false")
        self.assertEqual(metadata["tokenizer_free"], "true")

    def test_the_file_says_what_it_is_without_being_read_row_by_row(self):
        path, facts = self._write()
        _, metadata = read_sidecar(path)
        self.assertEqual(metadata["trajectory_id"], "seed-1")
        self.assertEqual(metadata["top_k"], str(TOP_K))
        self.assertEqual(metadata["segments"], ASSISTANT_SEGMENT)
        self.assertEqual(metadata["assistant_turns"], "3")
        self.assertEqual(metadata["token_rows"], "9")
        described = summary(path)
        self.assertEqual(described["tokens"], facts["tokens"])
        self.assertEqual(described["bytes"], facts["bytes"])
        self.assertEqual(described["sha256"], facts["sha256"])
        self.assertFalse(described["renormalized"])

    def test_the_prompt_the_distributions_were_produced_under_is_recorded(self):
        # Canonical trace messages omit a harness-owned system prompt, so
        # without this the sidecar would describe a distribution over a context
        # its consumer could not reconstruct.
        path, _ = self._write(system_prompt="You are a software engineer.")
        _, metadata = read_sidecar(path)
        self.assertEqual(metadata["system_prompt"],
                         "You are a software engineer.")

    def test_extra_provenance_travels_with_the_distributions(self):
        path, _ = self._write(extra_metadata={"model": "Qwen3", "base_url": "u"})
        _, metadata = read_sidecar(path)
        self.assertEqual(metadata["model"], "Qwen3")
        self.assertEqual(metadata["base_url"], "u")


class Alignment(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(
            tempfile.mkdtemp(prefix="moonshiner-align-"))
        self.path = self.directory / "trajectory.parquet"
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def _write(self, turns=TURNS) -> dict:
        return write_sidecar(self.path, trajectory_id="seed-1", turns=turns,
                             top_k=TOP_K)

    def test_every_row_lands_on_the_turn_that_generated_it(self):
        self._write()
        table, _ = read_sidecar(self.path)
        by_key = {(turn, index): token for turn, index, token in zip(
            table.column("assistant_turn_index").to_pylist(),
            table.column("token_index").to_pylist(),
            table.column("token_id").to_pylist())}
        for turn in TURNS:
            for index, token in enumerate(turn.tokens):
                self.assertEqual(
                    by_key[(turn.assistant_turn_index, index)], token.token_id)
        self.assertEqual(len(by_key), sum(len(turn.tokens) for turn in TURNS))

    def test_turn_numbering_is_the_published_assistant_step(self):
        # ``expand_next_steps`` numbers derived rows from 1 in message order;
        # a sidecar keyed any other way would need a translation table nobody
        # would notice was missing.
        self._write()
        table, _ = read_sidecar(self.path)
        covered = sorted(set(table.column("assistant_turn_index").to_pylist()))
        self.assertEqual(covered, [1, 2, 3])
        self.assertEqual(len(covered), assistant_turn_count(TRACE))

    def test_only_generated_tokens_have_rows_at_all(self):
        # Prompt and tool-result tokens carry no distribution. The boundary is
        # stated in the column rather than left to be inferred from an absence,
        # which is indistinguishable from a capture that dropped them.
        self._write()
        table, _ = read_sidecar(self.path)
        self.assertEqual(set(table.column("segment").to_pylist()),
                         {ASSISTANT_SEGMENT})
        self.assertEqual(table.num_rows, 9)
        report = alignment_report(TRACE, self.path)
        self.assertTrue(report["aligned"], report["problems"])
        self.assertEqual(report["assistant_turns_in_trace"], 3)
        self.assertEqual(report["assistant_turns_in_sidecar"], 3)
        self.assertEqual(report["tokens"], 9)
        self.assertEqual(report["top_k"], TOP_K)
        self.assertFalse(report["renormalized"])

    def test_a_sidecar_from_a_different_trace_is_reported(self):
        self._write([_turn(1, [11]), _turn(5, [51])])
        report = alignment_report(TRACE, self.path)
        self.assertFalse(report["aligned"])
        self.assertIn("1..5", report["problems"][0])

    def test_a_gap_inside_a_turn_is_reported(self):
        # Hand-damaged the way a partial copy or a filtered rewrite would
        # damage it: turn 1 keeps tokens 0 and 2, so index 1 is missing and
        # every target after it would train against its neighbour.
        self._write()
        table, _ = read_sidecar(self.path)
        keep = [index for index, (turn, position) in enumerate(zip(
            table.column("assistant_turn_index").to_pylist(),
            table.column("token_index").to_pylist()))
            if not (turn == 1 and position == 1)]
        pq.write_table(table.take(keep), self.path)
        report = alignment_report(TRACE, self.path)
        self.assertFalse(report["aligned"])
        self.assertIn("contiguous", report["problems"][0])


class RefusesToWriteSomethingMisleading(unittest.TestCase):
    def _write(self, turns, *, top_k=TOP_K, trajectory_id="seed-1"):
        directory = pathlib.Path(tempfile.mkdtemp(prefix="moonshiner-refuse-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return write_sidecar(directory / "t.parquet",
                             trajectory_id=trajectory_id, turns=turns,
                             top_k=top_k)

    def test_a_zero_based_turn_index_is_refused(self):
        with self.assertRaisesRegex(LogprobsUnavailable, "assistant_step"):
            self._write([_turn(0, [1])])

    def test_the_same_turn_cannot_be_captured_twice(self):
        with self.assertRaisesRegex(LogprobsUnavailable, "captured twice"):
            self._write([_turn(1, [1]), _turn(1, [2])])

    def test_ids_and_logprobs_must_pair_up(self):
        token = _token(7)
        token.top_logprobs = token.top_logprobs[:-1]
        with self.assertRaisesRegex(LogprobsUnavailable, "against"):
            self._write([TurnLogprobs(assistant_turn_index=1, tokens=[token])])

    def test_more_alternatives_than_requested_is_a_changed_objective(self):
        with self.assertRaisesRegex(LogprobsUnavailable, "requested top_logprobs=2"):
            self._write([_turn(1, [1])], top_k=2)

    def test_a_sidecar_needs_something_to_align_to(self):
        with self.assertRaisesRegex(LogprobsUnavailable, "trajectory id"):
            self._write(TURNS, trajectory_id="")


class Manifest(unittest.TestCase):
    def test_the_manifest_states_the_key_both_halves_join_on(self):
        directory = pathlib.Path(tempfile.mkdtemp(prefix="moonshiner-manifest-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        entries = [
            manifest_entry("task-b:abc", "logprobs/task-b__abc.parquet",
                           {"sha256": "b" * 64, "bytes": 200, "tokens": 20,
                            "assistant_turns": 2, "top_k": 100}),
            manifest_entry("task-a:def", "logprobs/task-a__def.parquet",
                           {"sha256": "a" * 64, "bytes": 100, "tokens": 10,
                            "assistant_turns": 1, "top_k": 100}),
        ]
        path = directory / "MANIFEST.json"
        document = write_manifest(path, entries)
        self.assertEqual(json.loads(path.read_text()), document)
        self.assertEqual(document["alignment"]["row_key"],
                         ["source_trajectory_id", "assistant_step"])
        self.assertEqual(document["alignment"]["sidecar_key"],
                         ["trajectory_id", "assistant_turn_index",
                          "token_index"])
        self.assertFalse(document["renormalized"])
        self.assertEqual(document["segments"], [ASSISTANT_SEGMENT])
        self.assertEqual(document["trajectories"], 2)
        self.assertEqual(document["tokens"], 30)
        self.assertEqual(document["bytes"], 300)
        self.assertEqual([entry["trajectory_id"] for entry in document["sidecars"]],
                         ["task-a:def", "task-b:abc"])


if __name__ == "__main__":
    unittest.main()
