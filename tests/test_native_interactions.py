import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from generate_traces import _trace_turns  # noqa: E402


class NativeInteractionTurns(unittest.TestCase):
    def test_plain_seed_prompt_is_unchanged(self):
        prompt, followups = _trace_turns({"prompt": "\nDo the task.\n"})
        self.assertEqual(prompt, "\nDo the task.\n")
        self.assertIsNone(followups)

    def test_every_authored_interaction_turn_runs_unchanged_and_in_order(self):
        seed = {
            "prompt": "Scenario description that is not a user turn.",
            "interaction": {"turns": [
                {"content": "First user turn."},
                {"content": "Second user turn."},
                {"content": "Third user turn."},
            ]},
        }
        prompt, followups = _trace_turns(seed)
        self.assertEqual(prompt, "First user turn.")
        self.assertEqual(followups, ["Second user turn.", "Third user turn."])


if __name__ == "__main__":
    unittest.main()
