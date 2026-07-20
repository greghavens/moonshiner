"""HF dataset card rendering: the card must reflect the ACTUAL mix, schema,
attestation, and teacher/judge config — no hand-maintained numbers."""
import json
import pathlib
import sys
import tempfile
import unittest
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
    def test_headline_size_uses_only_published_trace_data(self):
        with tempfile.TemporaryDirectory() as directory:
            traces = pathlib.Path(directory) / "traces.jsonl"
            traces.write_bytes(b"x" * 1_000)
            (pathlib.Path(directory) / "traces.jsonl.backup").write_bytes(b"x" * 10_000)
            with mock.patch.object(card, "TRACES", traces):
                text = card.build_card(_coding_and_security())
            self.assertIn("1 kB</h2>", text)

    def test_size_category_boundaries(self):
        self.assertEqual(card._size_category(3), "n<1K")
        self.assertEqual(card._size_category(1_000), "1K<n<10K")
        self.assertEqual(card._size_category(50_000), "10K<n<100K")

    def test_display_model_upcases_short_version(self):
        self.assertEqual(card._display_model("moonshotai/kimi-k3"), "Kimi K3")

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
        self.assertIn("Claude Fable 5", self.card)
        self.assertIn("Codex", self.card)

    def test_schema_and_mix_present(self):
        self.assertIn("## Schema", self.card)
        self.assertIn("`task`", self.card)
        self.assertIn("## Task mix", self.card)
        self.assertIn("Uncategorized", self.card)
        self.assertNotIn("## Tool surface", self.card)

    def test_security_domain_switches_on_security_framing(self):
        self.assertIn("question-answering", self.card)   # extra task category
        self.assertIn("owasp", self.card)                # security tag

    def test_coding_only_omits_security_framing(self):
        coding = [
            _row("t1", "py-build", "python", "build", "coding", "train", 1),
            _row("t2", "go-fix", "go", "debug", "coding", "val", 1),
        ]
        text = card.build_card(coding)
        self.assertNotIn("question-answering", text)
        self.assertNotIn("Authorization scope", text)
        self.assertIn("Coding & Debugging", text)

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
        self.assertIn("2 TRAINING ROWS", self.card)

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
