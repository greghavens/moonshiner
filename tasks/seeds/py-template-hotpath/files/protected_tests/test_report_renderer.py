from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest
from unittest.mock import patch

import report_renderer
from report_renderer import ReportRenderer, ReportTemplate


SOURCE = "{{account}} | {{region}} | ${{total}} | {{account}}\n"
ROWS = tuple(
    {
        "account": f"acct-{index:03d}",
        "region": "eu-west" if index % 2 else "us-east",
        "total": index * 17,
    }
    for index in range(40)
)
EXPECTED = "".join(
    f"acct-{index:03d} | "
    f"{'eu-west' if index % 2 else 'us-east'} | "
    f"${index * 17} | acct-{index:03d}\n"
    for index in range(40)
)


class ReportRendererCorrectnessTests(unittest.TestCase):
    def test_rendered_utf8_bytes_and_record_order_are_unchanged(self):
        rendered = ReportRenderer().render(ReportTemplate(SOURCE), ROWS)
        self.assertEqual(rendered.encode("utf-8"), EXPECTED.encode("utf-8"))

    def test_empty_input_is_empty(self):
        self.assertEqual(ReportRenderer().render(ReportTemplate(SOURCE), ()), "")

    def test_missing_field_keeps_exact_key_error(self):
        with self.assertRaises(KeyError) as caught:
            ReportRenderer().render(
                ReportTemplate("owner={{owner}}; zone={{zone}}\n"),
                ({"owner": "Ada"},),
            )
        self.assertEqual(caught.exception.args, ("zone",))

    def test_template_source_remains_immutable(self):
        template = ReportTemplate(SOURCE)
        with self.assertRaises(FrozenInstanceError):
            template.source = "changed"


class ReportRendererProfileTests(unittest.TestCase):
    def test_included_trace_identifies_repeated_parsing(self):
        trace = (
            Path(__file__).parents[1] / "profiles" / "report_renderer.prof.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("5000", trace)
        self.assertIn("parse_template", trace)
        self.assertIn("render_row", trace)

    def test_one_template_parses_at_most_once_across_renderers(self):
        real_parser = report_renderer.parse_template
        with patch.object(report_renderer, "parse_template", wraps=real_parser) as parser:
            template = ReportTemplate(SOURCE)
            first = ReportRenderer().render(template, ROWS)
            second = ReportRenderer().render(template, reversed(ROWS))

        self.assertEqual(first.encode("utf-8"), EXPECTED.encode("utf-8"))
        self.assertEqual(
            second.encode("utf-8"),
            "".join(reversed(EXPECTED.splitlines(keepends=True))).encode("utf-8"),
        )
        self.assertLessEqual(parser.call_count, 1)

    def test_equal_but_distinct_templates_have_independent_parse_lifecycles(self):
        real_parser = report_renderer.parse_template
        with patch.object(report_renderer, "parse_template", wraps=real_parser) as parser:
            first_template = ReportTemplate(SOURCE)
            second_template = ReportTemplate(SOURCE)
            first = ReportRenderer().render(first_template, ROWS[:2])
            second = ReportRenderer().render(second_template, ROWS[:2])

        self.assertEqual(first, second)
        self.assertEqual(parser.call_count, 2)


if __name__ == "__main__":
    unittest.main()
