"""Judge verdict extraction from model final text. Pure function, model-free.

A verdict lost to parsing rejects a good trace on a metered run, so every
wrapper shape a judge has produced gets a case: bare JSON, code fences,
surrounding prose, and a preliminary object before the definitive one (the
shape that silently rejected screened traces until the last-object scan).
"""
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from runtimes.claude_code import _parse_json  # noqa: E402

VERDICT = '{"verdict": "accept", "summary": "looks right"}'


class ParseJson(unittest.TestCase):
    def test_bare_object(self):
        self.assertEqual(_parse_json(VERDICT)["verdict"], "accept")

    def test_fenced_object(self):
        text = "```json\n" + VERDICT + "\n```"
        self.assertEqual(_parse_json(text)["verdict"], "accept")

    def test_prose_then_fenced_object(self):
        text = "Here is my verdict:\n\n```json\n" + VERDICT + "\n```\nDone."
        self.assertEqual(_parse_json(text)["verdict"], "accept")

    def test_preliminary_object_then_verdict_takes_last(self):
        text = '{"note": "working draft"}\n\n' + VERDICT
        self.assertEqual(_parse_json(text)["verdict"], "accept")

    def test_braces_inside_strings_do_not_derail(self):
        text = 'prefix {"detail": "use {curly} syntax"} suffix'
        self.assertEqual(_parse_json(text)["detail"], "use {curly} syntax")

    def test_malformed_fragment_then_valid_object(self):
        text = '{"broken": } then {"verdict": "reject"}'
        self.assertEqual(_parse_json(text)["verdict"], "reject")

    def test_empty_is_none(self):
        self.assertIsNone(_parse_json(""))
        self.assertIsNone(_parse_json("   \n"))

    def test_prose_only_is_none(self):
        self.assertIsNone(_parse_json("I could not produce a verdict."))

    def test_non_dict_json_is_none(self):
        self.assertIsNone(_parse_json('["accept"]'))


if __name__ == "__main__":
    unittest.main()
