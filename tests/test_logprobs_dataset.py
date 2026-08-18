"""What becomes of a captured trajectory's distributions after the trace ends.

The trace here is real: a fake chat-completions server drives the local backend
through a genuine sandboxed tool call, and everything downstream of it -- the
unjudged review, the build, the expansion, the export staging, the publication
file list, the size accounting -- is the production path.

What these tests defend is one property no training loader can check for
itself: a published row and the sidecar rows it trains against describe the
same generated tokens. Each test is either that alignment or a specific way of
losing it -- a sidecar that covers a different number of turns, bytes that
belong to some other generation, a stale file left behind by an earlier export.
A dataset that fails any of them still loads and still trains, which is exactly
why the failure has to be caught here.
"""
from __future__ import annotations

import copy
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import build_dataset  # noqa: E402
import common  # noqa: E402
import dataset_prep  # noqa: E402
import expand_next_steps  # noqa: E402
import export_hf_next_steps  # noqa: E402
import generate_traces  # noqa: E402
import publish  # noqa: E402
import screen_traces  # noqa: E402
from canonical_dataset import PUBLISH_KEY_ORDER  # noqa: E402
from logprobs_sidecar import file_sha256, read_sidecar  # noqa: E402
from runtimes.vllm import VLLMRuntime  # noqa: E402

TEST_TMP = pathlib.Path(os.environ.get(
    "TMPDIR", ROOT / ".moonshiner" / "test-tmp")).resolve()
TEST_TMP.mkdir(parents=True, exist_ok=True)
#: The model's workspace lives here rather than under TMPDIR: the write
#: boundary aliases /tmp into the workspace's own scratch, so a workspace that
#: itself sat under /tmp would be shadowed by that bind.
WORKSPACE_ROOT = ROOT / ".moonshiner" / "test-workspaces"

MODEL = "Qwen3-Coder-30B-A3B-Instruct"
TOP_K = 4
PROMPT = "Put the answer in answer.txt.\n"
#: Three tokens for the tool call, two for the closing message.
TURN_TOKENS = ([101, 102, 103], [201, 202])


def _entries(token_ids: list[int]) -> list[dict]:
    """One turn's logprobs block, with ids rendered the way vLLM renders them."""
    return [{"token": f"token_id:{token_id}", "logprob": -0.3,
             "top_logprobs": [{"token": f"token_id:{token_id + 1000 * rank}",
                               "logprob": -0.3 - rank}
                              for rank in range(TOP_K)]}
            for token_id in token_ids]


def _completion(*, content: str = "", tool_calls: list[dict] | None = None,
                token_ids: list[int], finish_reason: str = "stop") -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"id": "chatcmpl-1", "model": MODEL,
            "choices": [{"index": 0, "message": message,
                         "finish_reason": finish_reason,
                         "logprobs": {"content": _entries(token_ids)}}],
            "usage": {"prompt_tokens": 12,
                      "completion_tokens": len(token_ids),
                      "total_tokens": 12 + len(token_ids)}}


class _Server:
    """A fake OpenAI-compatible endpoint that answers in order."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, request, timeout=None):
        if not self.answers:
            raise AssertionError(f"unexpected extra request to {request.full_url}")
        return io.BytesIO(json.dumps(self.answers.pop(0)).encode())


def _runtime() -> VLLMRuntime:
    role = {"runtime": "vllm", "model": MODEL}
    config = {"teacher": role,
              "runtimes": {"vllm": {
                  "base_url": "http://127.0.0.1:8000/v1",
                  "sampling": {"temperature": 0.0, "max_tokens": 64},
                  "logprobs": {"enabled": True, "top_k": TOP_K}}}}
    return VLLMRuntime(config, role)


class _Distilled:
    """One captured trajectory, taken as far as the pipeline takes it."""

    def __init__(self, root: pathlib.Path):
        self.root = root
        self.traces = root / "traces"
        directory = root / "seed"
        (directory / "files").mkdir(parents=True)
        (directory / "files" / "baseline.txt").write_text("baseline\n")
        self.seed = {"id": "vllm-distill", "lang": "English",
                     "category": "Building", "prompt": PROMPT,
                     "verify_cmd": "test -f answer.txt", "_dir": directory}
        (directory / "task.json").write_text(json.dumps(
            {key: value for key, value in self.seed.items() if key != "_dir"}))

        server = _Server(
            _completion(tool_calls=[{
                "id": "call_1", "type": "function",
                "function": {"name": "write_file",
                             "arguments": json.dumps({"path": "answer.txt",
                                                      "content": "42\n"})}}],
                token_ids=TURN_TOKENS[0], finish_reason="tool_calls"),
            _completion(content="Wrote answer.txt.", token_ids=TURN_TOKENS[1]))
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        self.workspaces = pathlib.Path(tempfile.mkdtemp(
            prefix="dataset-", dir=WORKSPACE_ROOT))
        # The adapter refuses a workspace with an AGENTS.md above it, and the
        # offline gate keeps all test state inside the checkout, where that is
        # unavoidable. The precondition has its own test in
        # ``test_model_context_isolation``; the trace below it is real.
        with mock.patch("runtimes.vllm.urlopen", server), \
                mock.patch.object(common, "WORKSPACES", self.workspaces), \
                mock.patch.object(
                    VLLMRuntime, "require_persistent_workspace",
                    staticmethod(lambda path: pathlib.Path(path).resolve())):
            self.record = generate_traces.trace_task(
                self.seed, _runtime(), force=True, traces_root=self.traces,
                capability_resolution={"runtime": "vllm",
                                       "reason": "configured"})
            workspace = self.record.pop("_workspace_path", None)
            if workspace:
                common.remove_workspace(pathlib.Path(workspace))
        if not self.record.get("passed"):
            raise AssertionError(f"fixture trace did not pass: {self.record}")

        self.review = screen_traces.unjudged_review(self.seed,
                                                    traces_root=self.traces)
        self.row, error = build_dataset.build_row(self.seed, self.record,
                                                  traces_root=self.traces)
        if error:
            raise AssertionError(f"build refused a captured trace: {error}")
        self.derived = expand_next_steps.expand_record(self.row)
        self.published = [export_hf_next_steps.build_row(row, "train")
                          for row in self.derived]
        self.sidecar = self.traces / "logprobs" / "vllm-distill.parquet"


class _Staged(unittest.TestCase):
    """The trajectory is generated once; each test stages its own export."""

    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="logprob-corpus-",
                                                dir=TEST_TMP)
        cls.corpus = _Distilled(pathlib.Path(cls._temp.name))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.corpus.workspaces, ignore_errors=True)
        cls._temp.cleanup()

    def setUp(self):
        self.work = pathlib.Path(tempfile.mkdtemp(prefix="logprob-stage-",
                                                  dir=TEST_TMP))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.input_dir = self.work / "next_step"
        self.input_dir.mkdir(parents=True)
        self.write_rows(self.corpus.derived)
        self.output = self.work / "published" / "traces.jsonl"
        self.output.parent.mkdir(parents=True)
        self.output.write_text("".join(json.dumps(row) + "\n"
                                       for row in self.corpus.published))
        self.staged = self.output.parent / "logprobs"

    def write_rows(self, rows: list[dict]) -> pathlib.Path:
        path = self.input_dir / "train.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def stage(self, storage: pathlib.Path | None = None) -> dict:
        with mock.patch.object(export_hf_next_steps, "STORAGE_ROOT",
                               storage or self.corpus.root):
            return export_hf_next_steps.stage_sidecars(self.input_dir,
                                                       self.output)

    def damaged_storage(self) -> pathlib.Path:
        """A private copy of trace storage, for tests that break it."""
        target = self.work / "storage"
        shutil.copytree(self.corpus.root / "traces", target / "traces")
        return target

    def manifest(self) -> dict:
        return json.loads((self.staged / "MANIFEST.json").read_text())

    @property
    def sidecar_bytes(self) -> int:
        return self.corpus.sidecar.stat().st_size


class TheReferenceSurvivesEveryHopToPublication(_Staged):
    def test_the_built_row_points_at_the_sidecar_instead_of_carrying_it(self):
        reference = self.corpus.row["meta"]["logprobs"]
        self.assertEqual(reference["path"],
                         "traces/logprobs/vllm-distill.parquet")
        self.assertEqual(reference["sha256"], file_sha256(self.corpus.sidecar))
        self.assertEqual(reference["tokens"], sum(len(t) for t in TURN_TOKENS))
        self.assertEqual(reference["top_k"], TOP_K)
        self.assertEqual(reference["assistant_turns"], 2)
        self.assertEqual(reference["bytes"], self.sidecar_bytes)
        self.assertFalse(reference["renormalized"])
        # The row names the distributions; it never contains them. At the K
        # this backend exists to capture, inlining would put megabytes of
        # alternatives into a file people read by eye and load line by line.
        serialized = json.dumps(self.corpus.row)
        self.assertNotIn("top_logprobs", serialized)
        self.assertNotIn("top_token_ids", serialized)

    def test_the_row_says_no_judge_stood_behind_it(self):
        meta = self.corpus.row["meta"]
        self.assertEqual(meta["verifier"], build_dataset.UNJUDGED_VERIFIER)
        self.assertNotIn("independent-review", meta["verifier"])
        self.assertEqual(meta["screening"], screen_traces.UNJUDGED_SCREENING)
        self.assertTrue(self.corpus.review["judge"]["bypassed"])

    def test_every_derived_row_keeps_the_key_that_finds_its_distributions(self):
        reference = self.corpus.row["meta"]["logprobs"]
        steps = [row["meta"]["assistant_step"] for row in self.corpus.derived]
        self.assertEqual(steps, [1, 2])
        for row in self.corpus.derived:
            self.assertEqual(row["meta"]["logprobs"], reference)

        table, _ = read_sidecar(self.corpus.sidecar)
        turns = table.column("assistant_turn_index").to_pylist()
        sampled = table.column("token_id").to_pylist()
        # ``assistant_step`` is the sidecar's ``assistant_turn_index``: the two
        # halves of the join key are the same number, and this is the only
        # place both are in hand to say so.
        self.assertEqual(sorted(set(turns)), steps)
        for row, expected in zip(self.corpus.derived, TURN_TOKENS):
            step = row["meta"]["assistant_step"]
            self.assertEqual([token for token, turn in zip(sampled, turns)
                              if turn == step], expected)

    def test_publication_adds_no_column_and_still_carries_the_join_key(self):
        for published, derived in zip(self.corpus.published,
                                      self.corpus.derived):
            self.assertEqual(list(published), PUBLISH_KEY_ORDER)
            self.assertNotIn("logprobs", published)
            self.assertEqual(published["source_trajectory_id"],
                             derived["meta"]["source_trajectory_id"])
            self.assertEqual(published["assistant_step"],
                             derived["meta"]["assistant_step"])
        summary = export_hf_next_steps.validate_export(self.output)
        self.assertEqual(summary["trajectories"], 1)

    def test_staging_copies_the_sidecar_and_records_how_to_join_it(self):
        result = self.stage()
        self.assertEqual(result["stale"], [])
        self.assertEqual(result["trajectories"], 1)
        self.assertEqual(result["tokens"], sum(len(t) for t in TURN_TOKENS))
        self.assertEqual(result["bytes"], self.sidecar_bytes)

        document = self.manifest()
        self.assertEqual(document["alignment"]["row_key"],
                         ["source_trajectory_id", "assistant_step"])
        self.assertEqual(document["alignment"]["sidecar_key"],
                         ["trajectory_id", "assistant_turn_index",
                          "token_index"])
        self.assertFalse(document["renormalized"])
        entry, = document["sidecars"]
        self.assertEqual(entry["trajectory_id"],
                         self.corpus.published[0]["source_trajectory_id"])
        self.assertEqual(entry["assistant_turns"], 2)
        self.assertEqual(entry["top_k"], TOP_K)
        copied = self.output.parent / entry["path"]
        self.assertEqual(copied.read_bytes(),
                         self.corpus.sidecar.read_bytes())
        self.assertEqual(entry["sha256"], file_sha256(copied))

    def test_a_publication_uploads_the_manifest_and_every_sidecar_it_names(self):
        self.stage()
        card = self.output.parent / "README.md"
        card.write_text("# card\n")
        entry, = self.manifest()["sidecars"]
        copied = self.output.parent / entry["path"]
        self.assertEqual(
            publish.publication_files(self.output.parent, "jsonl",
                                      include_jsonl=True),
            [card, self.output, self.staged / "MANIFEST.json", copied])

        # A manifest naming a sidecar that was never uploaded describes
        # distributions the consumer cannot fetch, and their loader is where
        # that would surface. It stops the publication instead.
        copied.unlink()
        with self.assertRaisesRegex(ValueError, "required publication artifact"):
            publish.publication_files(self.output.parent, "jsonl",
                                      include_jsonl=True)


class MisalignmentIsCaughtBeforeItIsPublished(_Staged):
    def test_a_sidecar_covering_other_turns_stops_the_expansion(self):
        self.assertEqual(len(expand_next_steps.expand_record(self.corpus.row)),
                         2)
        mismatched = copy.deepcopy(self.corpus.row)
        mismatched["meta"]["logprobs"]["assistant_turns"] = 3
        with self.assertRaisesRegex(ValueError, "misaligned"):
            expand_next_steps.expand_record(mismatched)

    def test_bytes_that_no_longer_match_the_trace_are_dropped_and_reported(self):
        storage = self.damaged_storage()
        sidecar = storage / "traces" / "logprobs" / "vllm-distill.parquet"
        sidecar.write_bytes(sidecar.read_bytes() + b"a later capture")
        result = self.stage(storage)
        self.assertEqual(result["trajectories"], 0)
        self.assertEqual(len(result["stale"]), 1)
        self.assertIn("hash does not match", result["stale"][0])
        self.assertFalse(self.staged.exists())

    def test_a_missing_sidecar_is_reported_rather_than_quietly_skipped(self):
        storage = self.damaged_storage()
        (storage / "traces" / "logprobs" / "vllm-distill.parquet").unlink()
        result = self.stage(storage)
        self.assertEqual(result["trajectories"], 0)
        self.assertIn("sidecar is missing", result["stale"][0])
        self.assertFalse(self.staged.exists())

    def test_an_export_that_drops_a_trajectory_drops_its_sidecar(self):
        self.stage()
        orphan = self.staged / "retired__trajectory.parquet"
        orphan.write_bytes(b"from an earlier export")
        result = self.stage()
        self.assertEqual(result["trajectories"], 1)
        self.assertFalse(orphan.exists())
        self.assertEqual(len(self.manifest()["sidecars"]), 1)

    def test_an_export_with_no_capture_leaves_no_manifest_behind(self):
        self.stage()
        self.assertTrue(self.staged.exists())
        uncaptured = copy.deepcopy(self.corpus.derived)
        for row in uncaptured:
            row["meta"].pop("logprobs")
        self.write_rows(uncaptured)
        result = self.stage()
        self.assertEqual(result, {"trajectories": 0, "tokens": 0, "bytes": 0,
                                  "stale": []})
        self.assertFalse(self.staged.exists())
        self.assertEqual(publish.logprob_sidecar_files(self.output.parent), [])

    def test_a_stale_remote_sidecar_is_deleted_in_either_format(self):
        remote = {"traces.jsonl", "logprobs/MANIFEST.json",
                  "logprobs/kept.parquet", "logprobs/gone.parquet"}
        active = {"traces.jsonl", "logprobs/MANIFEST.json",
                  "logprobs/kept.parquet"}
        for mode in ("jsonl", "parquet-shards"):
            with self.subTest(mode=mode):
                deleted = publish.inactive_remote_paths(mode, remote, active)
                self.assertIn("logprobs/gone.parquet", deleted)
                self.assertNotIn("logprobs/kept.parquet", deleted)
                self.assertNotIn("logprobs/MANIFEST.json", deleted)


class StorageIsCountedOncePerTrajectory(_Staged):
    def test_derived_rows_do_not_multiply_the_sidecar_they_share(self):
        source = self.input_dir / "train.jsonl"
        report = dataset_prep.analyze_sources([str(source)])
        storage = report["storage"]
        self.assertEqual(report["summary"]["rows"], 2)
        self.assertEqual(report["summary"]["trajectories"], 1)
        # Both rows reference the same file. Summed per row this would report
        # the corpus as twice its size, which is the same class of error as
        # leaving the distributions out of the accounting entirely.
        self.assertEqual(storage["logprob_trajectories"], 1)
        self.assertEqual(storage["logprob_sidecar_bytes"], self.sidecar_bytes)
        self.assertEqual(storage["logprob_sidecar_tokens"],
                         sum(len(turn) for turn in TURN_TOKENS))
        self.assertEqual(storage["logprob_top_k"], [TOP_K])
        self.assertEqual(storage["total_bytes"],
                         storage["rows_bytes"] + self.sidecar_bytes)
        self.assertNotIn("counted_trajectories", storage)

    def test_a_published_dataset_is_not_reported_as_its_text_alone(self):
        self.stage()
        report = dataset_prep.analyze_sources([str(self.output)])
        storage = report["storage"]
        # Published rows deliberately carry no sidecar column, so row-level
        # accounting alone sees none of this. The manifest beside them is
        # where the size lives.
        self.assertEqual(storage["logprob_trajectories"], 1)
        self.assertEqual(storage["logprob_sidecar_bytes"], self.sidecar_bytes)
        self.assertEqual(storage["logprob_top_k"], [TOP_K])
        self.assertEqual(storage["total_bytes"],
                         storage["rows_bytes"] + self.sidecar_bytes)
        self.assertGreater(storage["total_bytes"], storage["rows_bytes"])

    def test_the_same_trajectory_seen_twice_is_counted_once(self):
        self.stage()
        report = dataset_prep.analyze_sources(
            [str(self.input_dir / "train.jsonl"), str(self.output)])
        storage = report["storage"]
        self.assertEqual(storage["logprob_trajectories"], 1)
        self.assertEqual(storage["logprob_sidecar_bytes"], self.sidecar_bytes)

    def test_a_source_without_a_manifest_reports_no_distributions(self):
        report = dataset_prep.analyze_sources([str(self.output)])
        self.assertEqual(report["storage"]["logprob_trajectories"], 0)
        self.assertEqual(report["storage"]["logprob_sidecar_bytes"], 0)
        self.assertEqual(report["storage"]["total_bytes"],
                         report["storage"]["rows_bytes"])

    def test_the_analysis_version_marks_the_report_that_counts_them(self):
        report = dataset_prep.analyze_sources(
            [str(self.input_dir / "train.jsonl")])
        self.assertEqual(dataset_prep.ANALYSIS_VERSION, 2)
        self.assertEqual(report["analysis_version"], 2)


if __name__ == "__main__":
    unittest.main()
