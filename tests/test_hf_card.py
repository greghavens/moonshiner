"""HF dataset card rendering: the card must reflect the ACTUAL mix, schema,
attestation, and teacher/judge config — no hand-maintained numbers."""
import json
import pathlib
import sys
import tempfile
import unittest
from collections import Counter
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import export_hf_card as card  # noqa: E402
from common import CONFIG  # noqa: E402


def _row(tid, task, lang, category, domain, split, steps, attested=True):
    return {
        "task": task,
        "source_trajectory_id": tid,
        "lang": lang,
        "category": category,
        "domain": domain,
        "split": split,
        "assistant_steps": steps,
        "tools_used": ["read", "edit", "bash"],
        "provider": "openrouter",
        "model_attested": attested,
        "derivation": "cumulative-next-step-prefixes",
        "tools": json.dumps([
            {"type": "function", "function": {"name": "read"}},
            {"type": "function", "function": {"name": "bash"}},
        ]),
    }


def _coding_and_security():
    return [
        _row("t1", "py-build", "python", "build", "coding", "train", 2),
        _row("t1", "py-build", "python", "build", "coding", "train", 2),
        _row("t2", "py-audit", "python", "security", "security", "val", 1),
    ]


class Units(unittest.TestCase):
    def test_whole_trajectory_counts_each_assistant_turn_as_a_training_row(self):
        row = _preview_row("multi-turn", "en", "tool-calling")
        text = card.build_card([row])
        self.assertIn("1 TRAJECTORIES · 2 TRAINING ROWS", text)

    def test_cumulative_prefix_counts_each_stored_record_once(self):
        text = card.build_card(_coding_and_security())
        self.assertIn("2 TRAJECTORIES · 3 TRAINING ROWS", text)

    def test_kimi_banner_is_packaged_at_the_configurable_asset_path(self):
        banner = _ROOT / "assets" / "kimi-k3-dataset-banner.png"
        self.assertTrue(banner.is_file())
        header = banner.read_bytes()[:24]
        self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int.from_bytes(header[16:20], "big"), 2048)
        self.assertEqual(int.from_bytes(header[20:24], "big"), 820)

    def test_headline_size_uses_only_published_trace_data(self):
        with tempfile.TemporaryDirectory() as directory:
            traces = pathlib.Path(directory) / "traces.jsonl"
            traces.write_bytes(b"x" * 1_000)
            (pathlib.Path(directory) / "traces.jsonl.backup").write_bytes(b"x" * 10_000)
            with mock.patch.object(card, "TRACES", traces):
                text = card.build_card(_coding_and_security())
            self.assertIn("1 kB</h2>", text)

    def test_parquet_headline_uses_active_manifest_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "dataset-manifest.json").write_text(json.dumps({
                "bytes": 2_000, "row_count": 3, "trajectory_count": 2}))
            config = {**CONFIG, "publish": {
                **CONFIG.get("publish", {}), "format": "parquet-shards"}}
            with mock.patch.object(card, "CONFIG", config), \
                 mock.patch.object(card, "PUBLISH_DIR", root):
                text = card.build_card(_coding_and_security())
            self.assertIn("2 kB</h2>", text)
            self.assertIn("active Parquet shards", text)

    def test_size_category_boundaries(self):
        self.assertEqual(card._size_category(3), "n<1K")
        self.assertEqual(card._size_category(1_000), "1K<n<10K")
        self.assertEqual(card._size_category(50_000), "10K<n<100K")

    def test_display_model_upcases_short_version(self):
        self.assertEqual(card._display_model("moonshotai/kimi-k3"), "Kimi K3")

    def test_project_can_select_a_model_specific_banner(self):
        config = {"publish": {"banner_source": "assets/kimi-k3-dataset-banner.png"}}
        with mock.patch.object(card, "CONFIG", config), \
                mock.patch.object(card, "ROOT", pathlib.Path("/package")):
            self.assertEqual(card._banner_source(),
                             pathlib.Path("/package/assets/kimi-k3-dataset-banner.png"))

class Card(unittest.TestCase):
    def setUp(self):
        self.card = card.build_card(_coding_and_security())

    def test_front_matter_and_size(self):
        self.assertTrue(self.card.startswith("---\n"))
        for field in ("pretty_name:", "license:", "size_categories:", "tags:"):
            self.assertIn(field, self.card)
        self.assertIn("n<1K", self.card)  # 3 rows

    def test_snapshot_counts_follow_data(self):
        # 3 rows across 2 trajectories, all attested.
        self.assertIn("2 TRAJECTORIES", self.card)
        self.assertIn("3 TRAINING ROWS", self.card)
        self.assertLess(self.card.index("3 TRAINING ROWS"),
                        self.card.index("github.com/greghavens/moonshiner"))
        self.assertLess(self.card.index("github.com/greghavens/moonshiner"),
                        self.card.index("Behavior-preserving"))

    def test_teacher_and_judge_from_config(self):
        config = {
            **CONFIG,
            "teacher": {**CONFIG.get("teacher", {}),
                        "model": "anthropic/claude-fable-5"},
        }
        with mock.patch.object(card, "CONFIG", config):
            text = card.build_card(_coding_and_security())
        self.assertIn("Claude Fable 5", text)
        self.assertIn("Codex", text)

    def test_kimi_project_card_contains_no_fable_branding(self):
        config = {
            **CONFIG,
            "teacher": {**CONFIG.get("teacher", {}),
                        "model": "moonshotai/kimi-k3"},
            "publish": {**CONFIG.get("publish", {}),
                        "hf_dataset": "greghavens/kimi-k3-coding-and-debugging-traces",
                        "pretty_name": "Kimi K3 Coding, Tool Use & Instruction Following Traces",
                        "model_display": "Kimi K3"},
        }
        with mock.patch.object(card, "CONFIG", config):
            text = card.build_card(_coding_and_security())
        self.assertIn("Kimi K3", text)
        self.assertIn("`moonshotai/kimi-k3`", text)
        self.assertNotIn("Fable", text)
        self.assertNotIn("claude-fable-5", text)

    def test_schema_and_mix_present(self):
        self.assertIn("## Schema", self.card)
        self.assertIn("`task`", self.card)
        self.assertIn("## Task mix", self.card)
        self.assertIn("| Build |", self.card)
        self.assertIn("| Security |", self.card)
        self.assertNotIn("| Uncategorized |", self.card)
        self.assertNotIn("## Tool surface", self.card)

    def test_task_mix_includes_row_share_without_row_counts(self):
        table = card._program_table(
            Counter({"Tool calling": 2, "Building": 1}), 3,
            Counter({"Tool calling": 2, "Building": 4}), 6)
        self.assertIn(
            "| kind | trajectories | share | row share | flavor |", table)
        self.assertIn("| Tool calling | 2 | 66.7% | 33.3% |", table)
        self.assertIn("| Building | 1 | 33.3% | 66.7% |", table)
        self.assertNotIn("| rows |", table)

    def test_uncatalogued_rows_keep_their_explicit_category(self):
        rows = [
            _row("author-1", "seed-author-w5", "seed-authoring",
                 "seed-authoring", None, "train", 2),
            _row("author-1", "seed-author-w5", "seed-authoring",
                 "seed-authoring", None, "train", 2),
        ]
        with mock.patch.object(card, "_program_assignments", return_value={}):
            text = card.build_card(rows)
        self.assertIn("| Seed authoring | 1 | 100.0% | 100.0% |", text)
        self.assertNotIn("| Uncategorized |", text)

    def test_installed_catalog_supplements_missing_source_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            data = pathlib.Path(directory) / "data"
            active = data.parent / "corpora" / "active"
            active.mkdir(parents=True)
            (active / "SEED_CATALOG.json").write_text(json.dumps({
                "categories": {"debug": [{
                    "id": "bash-it-certinventory", "program": "Debugging"
                }]}
            }))
            with mock.patch.object(card, "DATA", data), \
                    mock.patch.object(card, "ROOT", pathlib.Path(directory) / "missing"):
                assignments = card._program_assignments()
        self.assertEqual(assignments["bash-it-certinventory"], "Debugging")

    def test_security_domain_switches_on_security_framing(self):
        self.assertIn("question-answering", self.card)   # extra task category
        self.assertIn("owasp", self.card)                # security tag

    def test_coding_only_omits_security_framing(self):
        coding = [
            _row("t1", "py-build", "python", "build", "coding", "train", 1),
            _row("t2", "go-fix", "go", "debug", "coding", "val", 1),
        ]
        clean_config = {**card.CONFIG, "teacher": {"model": ""}, "publish": {}}
        with mock.patch.object(card, "CONFIG", clean_config):
            text = card.build_card(coding)
        self.assertNotIn("question-answering", text)
        self.assertNotIn("Authorization scope", text)
        self.assertIn("Configured Model Agent Traces", text)
        self.assertIn("| Build |", text)
        self.assertIn("| Debug |", text)

    def test_banner_and_numbers_are_directly_below_title(self):
        for text in (self.card, card.build_card([], stage="preview")):
            h1_end = text.index("\n", text.index("\n# ") + 1)
            below = text[h1_end:].lstrip()
            self.assertTrue(below.startswith("![Moonshiner"))
            self.assertIn("TRAJECTORIES ·", below)


def _preview_row(task, lang, category):
    return {
        "task": task,
        "lang": lang,
        "category": category,
        "teacher_runtime": "pi",
        "teacher_model": "moonshotai/kimi-k3",
        "provider": "openrouter",
        "reasoning_effort": "max",
        "model_attested": True,
        "observed_models": ["moonshotai/kimi-k3"],
        "trace_format": "pi-coding-agent-json-v3",
        "n_messages": 4,
        "messages": [
            {"role": "user", "content": "fix it"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ],
    }


class PreviewCard(unittest.TestCase):
    def setUp(self):
        rows = [_preview_row("asm-bjval", "asm", "build-game"),
                _preview_row("bash-argfwd", "bash", "debug-cli")]
        self.card = card.build_card(rows, stage="preview")

    def test_stage_is_validated(self):
        with self.assertRaises(ValueError):
            card.build_card([], stage="draft")

    def test_in_progress_snapshot_no_release_claims(self):
        self.assertIn("2 TRAJECTORIES", self.card)
        self.assertIn("4 TRAINING ROWS", self.card)

    def test_preview_schema_and_mix_from_rows(self):
        self.assertIn("Building", self.card)
        self.assertIn("Debugging", self.card)
        self.assertIn("`Assembly`", self.card)
        self.assertIn("`Bash`", self.card)

    def test_empty_preview_renders(self):
        text = card.build_card([], stage="preview")
        self.assertIn("0 TRAJECTORIES", text)
        self.assertNotIn("| 0.0% |", text)


if __name__ == "__main__":
    unittest.main()
