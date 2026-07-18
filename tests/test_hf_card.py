"""HF dataset card rendering: the card must reflect the ACTUAL mix, schema,
attestation, and teacher/judge config — no hand-maintained numbers."""
import json
import pathlib
import sys
import unittest

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
    def test_size_category_boundaries(self):
        self.assertEqual(card._size_category(3), "n<1K")
        self.assertEqual(card._size_category(1_000), "1K<n<10K")
        self.assertEqual(card._size_category(50_000), "10K<n<100K")

    def test_display_model_upcases_short_version(self):
        self.assertEqual(card._display_model("moonshotai/kimi-k3"), "Kimi K3")

    def test_model_tags_progressive_family(self):
        tags = card._model_tags("moonshotai/kimi-k3", "openrouter")
        for expected in ("moonshotai", "kimi", "k3", "kimi-k3", "openrouter",
                         "pi-coding-agent"):
            self.assertIn(expected, tags)


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
        self.assertIn("3 next-step rows", self.card)
        self.assertIn("2 accepted trajectories", self.card)
        self.assertIn("**100%**", self.card)

    def test_teacher_and_judge_from_config(self):
        self.assertIn(CONFIG["teacher"]["model"], self.card)
        self.assertIn(CONFIG["judge"]["model"], self.card)

    def test_schema_and_mix_present(self):
        self.assertIn("## Schema", self.card)
        self.assertIn("source_trajectory_id", self.card)
        self.assertIn("## Task program", self.card)
        self.assertIn("`build`", self.card)
        self.assertIn("`read`", self.card)  # offered tool surface

    def test_security_domain_switches_on_security_framing(self):
        self.assertIn("question-answering", self.card)   # extra task category
        self.assertIn("owasp", self.card)                # security tag
        self.assertIn("Authorization scope", self.card)  # security limitation

    def test_coding_only_omits_security_framing(self):
        coding = [
            _row("t1", "py-build", "python", "build", "coding", "train", 1),
            _row("t2", "go-fix", "go", "debug", "coding", "val", 1),
        ]
        text = card.build_card(coding)
        self.assertNotIn("question-answering", text)
        self.assertNotIn("Authorization scope", text)
        self.assertIn("Coding & Debugging", text)


if __name__ == "__main__":
    unittest.main()
