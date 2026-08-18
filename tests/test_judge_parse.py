"""Judge verdict extraction from model final text. Pure function, model-free.

A verdict lost to parsing rejects a good trace on a metered run, so every
wrapper shape a judge has produced gets a case: bare JSON, code fences,
surrounding prose, and a preliminary object before the definitive one (the
shape that silently rejected screened traces until the last-object scan).

Run against every adapter's spelling of the extractor, because each one had
its own copy and the same bug had to be found once per harness: OpenCode's
took the first ``{`` to the last ``}`` and lost a complete review to a request
body the judge had quoted in its own findings.
"""
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from runtimes.base import parse_json_verdict  # noqa: E402
from runtimes.claude_code import _parse_json  # noqa: E402
from runtimes.codex import _parse_json_object as _codex_parse  # noqa: E402
from runtimes.opencode import _parse_json_object as _opencode_parse  # noqa: E402

VERDICT = '{"verdict": "accept", "summary": "looks right"}'

EXTRACTORS = {"base": parse_json_verdict, "claude-code": _parse_json,
              "codex": _codex_parse, "opencode": _opencode_parse}


class ParseJson(unittest.TestCase):
    def parse(self, text):
        """Parse with every adapter's extractor; they must agree."""
        results = {name: extract(text) for name, extract in EXTRACTORS.items()}
        for name, value in results.items():
            with self.subTest(runtime=name):
                self.assertEqual(value, results["base"])
        return results["base"]

    def test_bare_object(self):
        self.assertEqual(self.parse(VERDICT)["verdict"], "accept")

    def test_fenced_object(self):
        text = "```json\n" + VERDICT + "\n```"
        self.assertEqual(self.parse(text)["verdict"], "accept")

    def test_prose_then_fenced_object(self):
        text = "Here is my verdict:\n\n```json\n" + VERDICT + "\n```\nDone."
        self.assertEqual(self.parse(text)["verdict"], "accept")

    def test_preliminary_object_then_verdict_takes_last(self):
        text = '{"note": "working draft"}\n\n' + VERDICT
        self.assertEqual(self.parse(text)["verdict"], "accept")

    def test_braces_inside_strings_do_not_derail(self):
        text = 'prefix {"detail": "use {curly} syntax"} suffix'
        self.assertEqual(self.parse(text)["detail"], "use {curly} syntax")

    def test_malformed_fragment_then_valid_object(self):
        text = '{"broken": } then {"verdict": "reject"}'
        self.assertEqual(self.parse(text)["verdict"], "reject")

    def test_findings_that_quote_json_do_not_eat_the_verdict(self):
        # The review that was actually lost: Opus 5 wrote out its findings,
        # quoting a request body and a `{id}` path placeholder, then closed
        # with the verdict in a fence. First-brace-to-last-brace spanned the
        # lot, parsed as nothing, and the queue stopped on "no verdict
        # returned" — with a complete, well-formed verdict sitting in the text.
        text = ("**Findings:**\n"
                "- `PATCH /v1/bundles/{id}` with exactly "
                '`{"bundleDownloadSpec":{"downloadNow":true}}`\n'
                "- Unknown bundle 404s with no submission.\n\n"
                "```json\n" + VERDICT + "\n```")
        self.assertEqual(self.parse(text)["verdict"], "accept")

    def test_empty_is_none(self):
        self.assertIsNone(self.parse(""))
        self.assertIsNone(self.parse("   \n"))

    def test_prose_only_is_none(self):
        self.assertIsNone(self.parse("I could not produce a verdict."))

    def test_non_dict_json_is_none(self):
        self.assertIsNone(self.parse('["accept"]'))


class OneExtractorForEveryHarness(unittest.TestCase):
    """Three copies meant finding this bug three times."""

    def test_no_adapter_keeps_its_own_version(self):
        for name, extract in EXTRACTORS.items():
            with self.subTest(runtime=name):
                self.assertIs(extract, parse_json_verdict)


if __name__ == "__main__":
    unittest.main()
