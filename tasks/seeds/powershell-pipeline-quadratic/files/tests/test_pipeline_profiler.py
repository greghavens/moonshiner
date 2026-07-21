#!/usr/bin/env python3
"""Hermetic contract tests for the PowerShell pipeline buffer repair."""

from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "PipelineProfiler.psm1"

COUNTER = (
    r"\$OperationCounter\s*\[\s*"
    r"(?:'StorageOperations'|\"StorageOperations\")\s*\]"
)
BUFFER_TYPE = (
    r"\[\s*System\.Collections\.Generic\.List\s*"
    r"\[\s*object\s*\]\s*\]"
)


def mask_comments_and_strings(source: str) -> str:
    """Keep offsets while hiding PowerShell comments and quoted strings."""
    masked = list(source)
    index = 0
    quote: str | None = None
    while index < len(source):
        char = source[index]
        if quote is not None:
            masked[index] = " "
            if char == "`" and quote == '"' and index + 1 < len(source):
                index += 1
                masked[index] = " "
            elif char == quote:
                if index + 1 < len(source) and source[index + 1] == quote:
                    index += 1
                    masked[index] = " "
                else:
                    quote = None
        elif source.startswith("<#", index):
            masked[index:index + 2] = "  "
            index += 2
            while index < len(source) and not source.startswith("#>", index):
                if source[index] not in "\r\n":
                    masked[index] = " "
                index += 1
            if index < len(source):
                masked[index:index + 2] = "  "
                index += 2
            continue
        elif char in {"'", '"'}:
            quote = char
            masked[index] = " "
        elif char == "#":
            while index < len(source) and source[index] not in "\r\n":
                masked[index] = " "
                index += 1
            continue
        index += 1
    return "".join(masked)


def remove_comments(source: str) -> str:
    """Remove PowerShell comments while retaining strings and line layout."""
    cleaned = list(source)
    index = 0
    quote: str | None = None
    while index < len(source):
        char = source[index]
        if quote is not None:
            if char == "`" and quote == '"' and index + 1 < len(source):
                index += 1
            elif char == quote:
                if index + 1 < len(source) and source[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif source.startswith("<#", index):
            cleaned[index:index + 2] = "  "
            index += 2
            while index < len(source) and not source.startswith("#>", index):
                if source[index] not in "\r\n":
                    cleaned[index] = " "
                index += 1
            if index < len(source):
                cleaned[index:index + 2] = "  "
                index += 2
            continue
        elif char in {"'", '"'}:
            quote = char
        elif char == "#":
            while index < len(source) and source[index] not in "\r\n":
                cleaned[index] = " "
                index += 1
            continue
        index += 1
    return "".join(cleaned)


def matching_delimiter(masked: str, opening: int, left: str, right: str) -> int:
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == left:
            depth += 1
        elif masked[index] == right:
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError(f"unclosed {left!r} delimiter")


def named_block(body: str, name: str) -> tuple[str, int, int]:
    masked = mask_comments_and_strings(body)
    matches = list(re.finditer(rf"(?<![\w-]){name}(?![\w-])\s*\{{", masked, re.I))
    if len(matches) != 1:
        raise AssertionError(f"expected one {name} block, found {len(matches)}")
    opening = masked.find("{", matches[0].start())
    closing = matching_delimiter(masked, opening, "{", "}")
    return body[opening + 1:closing], matches[0].start(), closing + 1


def split_top_level(text: str, separator: str = ",") -> list[str]:
    masked = mask_comments_and_strings(text)
    depths = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    start = 0
    parts: list[str] = []
    for index, char in enumerate(masked):
        if char in depths:
            depths[char] += 1
        elif char in pairs:
            depths[pairs[char]] -= 1
        elif char == separator and all(depth == 0 for depth in depths.values()):
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def parse_signature(body: str) -> tuple[str, ...]:
    masked = mask_comments_and_strings(body)
    param_match = re.search(r"(?<![\w-])param(?![\w-])\s*\(", masked, re.I)
    if param_match is None:
        raise AssertionError("Invoke-ProfiledPipeline has no param block")
    opening = masked.find("(", param_match.start())
    closing = matching_delimiter(masked, opening, "(", ")")
    entries = split_top_level(remove_comments(body[opening + 1:closing]))
    if len(entries) != 3:
        raise AssertionError(f"expected three public parameters, found {len(entries)}")

    expected = (
        ("InputObject", "object", ("parameter(mandatory,valuefrompipeline)", "allownull()")),
        ("Transform", "scriptblock", ("parameter(mandatory)",)),
        ("OperationCounter", "system.collections.idictionary", ("parameter(mandatory)",)),
    )
    parsed_names: list[str] = []
    for entry, (expected_name, expected_type, expected_attributes) in zip(entries, expected):
        variables = re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", entry)
        if variables != [expected_name]:
            raise AssertionError(f"public parameter {expected_name} changed")
        attributes = re.findall(r"\[\s*([^\[\]]+?)\s*\]", entry)
        if not attributes:
            raise AssertionError(f"public parameter {expected_name} lost its type")
        normalized = tuple(re.sub(r"\s+", "", item).lower() for item in attributes)
        if normalized[-1] != expected_type:
            raise AssertionError(f"public parameter {expected_name} changed type")
        if normalized[:-1] != expected_attributes:
            raise AssertionError(f"public parameter {expected_name} changed attributes")
        residual = re.sub(r"\[\s*[^\[\]]+?\s*\]", " ", entry)
        residual = re.sub(rf"\${expected_name}\b", " ", residual, count=1, flags=re.I)
        if residual.strip():
            raise AssertionError(f"public parameter {expected_name} gained a default or modifier")
        parsed_names.append(expected_name)
    return tuple(parsed_names)


def is_top_level(text: str, offset: int) -> bool:
    masked = mask_comments_and_strings(text[:offset])
    return masked.count("{") == masked.count("}")


def assert_only_spans(
    text: str,
    spans: Iterable[tuple[int, int]],
    message: str,
) -> None:
    residual = list(text)
    for start, stop in spans:
        residual[start:stop] = " " * (stop - start)
    if re.fullmatch(r"[\s;]*", "".join(residual)) is None:
        raise AssertionError(message)


@dataclass(frozen=True)
class PipelinePlan:
    parameters: tuple[str, ...]
    buffer: str
    transformed: str
    item: str
    count_before: str
    capacity_before: str
    storage_delta: str
    final_materialization: bool

    @staticmethod
    def storage_operations(item_count: int, initial: int = 0) -> int:
        count = 0
        capacity = 0
        operations = initial
        for _ in range(item_count):
            count_before = count
            if count == capacity:
                capacity = 4 if capacity == 0 else capacity * 2
                operations += count_before
            count += 1
            operations += 1
        return operations + count

    def invoke(
        self,
        inputs: Iterable[object],
        transform: Callable[[object], Iterable[object]],
    ) -> tuple[list[object], int]:
        buffered: list[object] = []
        for value in inputs:
            buffered.extend(transform(value))
        return buffered, self.storage_operations(len(buffered))


def parse_plan(source: str) -> PipelinePlan:
    masked_source = mask_comments_and_strings(source)
    function_matches = list(re.finditer(
        r"(?<![\w-])function\s+Invoke-ProfiledPipeline\s*\{",
        masked_source,
        re.I,
    ))
    if len(function_matches) != 1:
        raise AssertionError(
            f"expected one Invoke-ProfiledPipeline definition, found {len(function_matches)}"
        )
    preamble = remove_comments(source[:function_matches[0].start()])
    if re.fullmatch(
        r"\s*Set-StrictMode\s+-Version\s+Latest\s*",
        preamble,
        re.I,
    ) is None:
        raise AssertionError("the module preamble changed")
    function_open = masked_source.find("{", function_matches[0].start())
    function_close = matching_delimiter(masked_source, function_open, "{", "}")
    body = source[function_open + 1:function_close]
    masked_body = mask_comments_and_strings(body)

    parameters = parse_signature(body)
    begin, begin_start, begin_end = named_block(body, "begin")
    process, process_start, process_end = named_block(body, "process")
    end, end_start, end_end = named_block(body, "end")
    if not (begin_start < process_start < end_start):
        raise AssertionError("begin, process, and end blocks changed order")
    lifecycle_mask = list(masked_body)
    for start, stop in ((begin_start, begin_end), (process_start, process_end), (end_start, end_end)):
        lifecycle_mask[start:stop] = " " * (stop - start)
    remainder = "".join(lifecycle_mask)
    remainder = re.sub(r"\[\s*CmdletBinding\s*\(\s*\)\s*\]", " ", remainder, flags=re.I)
    param_match = re.search(r"(?<![\w-])param(?![\w-])\s*\(", remainder, re.I)
    if param_match is None:
        raise AssertionError("the public param block was not found")
    param_open = remainder.find("(", param_match.start())
    param_close = matching_delimiter(remainder, param_open, "(", ")")
    remainder = remainder[:param_match.start()] + " " * (param_close + 1 - param_match.start()) + remainder[param_close + 1:]
    if remainder.strip():
        raise AssertionError("unexpected executable content outside the lifecycle blocks")

    forbidden = re.compile(r"(?<![\w-])(?:try|catch|trap|clean|dynamicparam)(?![\w-])", re.I)
    if forbidden.search(masked_body):
        raise AssertionError("error handling or extra lifecycle blocks changed error propagation")

    begin_clean = remove_comments(begin)
    init_check = re.compile(
        rf"if\s*\(\s*-not\s+\$OperationCounter\.Contains\s*\(\s*"
        rf"(?:'StorageOperations'|\"StorageOperations\")\s*\)\s*\)\s*\{{\s*"
        rf"{COUNTER}\s*=\s*(?:\[\s*long\s*\]\s*)?0\s*\}}",
        re.I | re.S,
    )
    init_match = init_check.search(begin_clean)
    if init_match is None:
        raise AssertionError("StorageOperations initialization changed")

    list_match = re.search(
        rf"\$(?P<buffer>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*{BUFFER_TYPE}\s*"
        rf"::\s*new\s*\(\s*\)",
        begin_clean,
        re.I,
    )
    if list_match is None:
        raise AssertionError("the result buffer is not a default-constructed List[object]")
    assert_only_spans(
        begin_clean,
        ((init_match.start(), init_match.end()), (list_match.start(), list_match.end())),
        "begin must only initialize the counter and result buffer",
    )
    buffer = list_match.group("buffer")
    if len(re.findall(rf"\${re.escape(buffer)}\s*(?:\+?=)", remove_comments(body), re.I)) != 1:
        raise AssertionError("the result buffer is reassigned or concatenated")

    process_clean = remove_comments(process)
    transform_match = re.search(
        r"\$(?P<transformed>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*&\s*"
        r"\$Transform\s+\$InputObject",
        process_clean,
        re.I,
    )
    if transform_match is None:
        raise AssertionError("each input must be passed directly to Transform")
    if not is_top_level(process_clean, transform_match.start()):
        raise AssertionError("Transform invocation must be unconditional in process")
    transformed = transform_match.group("transformed")
    if len(re.findall(r"&\s*\$Transform\b", process_clean, re.I)) != 1:
        raise AssertionError("Transform must be invoked exactly once per input")

    process_masked = mask_comments_and_strings(process)
    foreach_match = re.search(
        rf"(?<![\w-])foreach\s*\(\s*\$(?P<item>[A-Za-z_][A-Za-z0-9_]*)\s+"
        rf"in\s+\${re.escape(transformed)}\s*\)\s*\{{",
        process_masked,
        re.I,
    )
    if foreach_match is None:
        raise AssertionError("transformed values are not iterated in pipeline order")
    if not is_top_level(process, foreach_match.start()):
        raise AssertionError("transformed output iteration must be unconditional")
    foreach_open = process_masked.find("{", foreach_match.start())
    foreach_close = matching_delimiter(process_masked, foreach_open, "{", "}")
    assert_only_spans(
        process_clean,
        (
            (transform_match.start(), transform_match.end()),
            (foreach_match.start(), foreach_close + 1),
        ),
        "process must only transform the input and buffer the emitted values",
    )
    loop = process[foreach_open + 1:foreach_close]
    loop_clean = remove_comments(loop)
    item = foreach_match.group("item")

    add_matches = list(re.finditer(
        rf"\${re.escape(buffer)}\.Add\s*\(\s*\${re.escape(item)}\s*\)",
        loop_clean,
        re.I,
    ))
    if len(add_matches) != 1 or not is_top_level(loop_clean, add_matches[0].start()):
        raise AssertionError("each transformed value must have one unconditional List.Add")
    add_at = add_matches[0].start()

    def prior_capture(property_name: str) -> tuple[str, re.Match[str]]:
        captures = [
            match for match in re.finditer(
                rf"\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                rf"\${re.escape(buffer)}\.{property_name}\b",
                loop_clean,
                re.I,
            )
            if match.start() < add_at and is_top_level(loop_clean, match.start())
        ]
        if len(captures) != 1:
            raise AssertionError(f"capture List.{property_name} exactly once before Add")
        return captures[0].group("name"), captures[0]

    count_before, count_match = prior_capture("Count")
    capacity_before, capacity_match = prior_capture("Capacity")

    loop_masked = mask_comments_and_strings(loop)
    growth_match = re.search(
        rf"if\s*\(\s*\${re.escape(buffer)}\.Capacity\s+-ne\s+"
        rf"\${re.escape(capacity_before)}\s*\)\s*\{{",
        loop_masked,
        re.I,
    )
    if growth_match is None or growth_match.start() < add_at or not is_top_level(loop, growth_match.start()):
        raise AssertionError("List growth must be detected after Add by comparing prior capacity")
    growth_open = loop_masked.find("{", growth_match.start())
    growth_close = matching_delimiter(loop_masked, growth_open, "{", "}")
    growth = remove_comments(loop[growth_open + 1:growth_close])

    delta_matches = [
        match for match in re.finditer(
            r"\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
            r"(?:\[\s*long\s*\]\s*)?1\b",
            loop_clean,
            re.I,
        )
        if match.start() > add_at and match.start() < growth_match.start()
        and is_top_level(loop_clean, match.start())
    ]
    if len(delta_matches) != 1:
        raise AssertionError("List.Add must contribute exactly one storage operation")
    storage_delta = delta_matches[0].group("name")
    growth_copy = re.search(
        rf"\${re.escape(storage_delta)}\s*\+=\s*\${re.escape(count_before)}\b",
        growth,
        re.I,
    )
    if growth_copy is None:
        raise AssertionError("a growth must count every pre-existing element copied")

    after_growth = loop_clean[growth_close + 1:]
    counter_update = re.search(
        rf"{COUNTER}\s*=\s*(?:\[\s*long\s*\]\s*)?{COUNTER}\s*"
        rf"\+\s*\${re.escape(storage_delta)}\b",
        after_growth,
        re.I,
    )
    if counter_update is None:
        raise AssertionError("the per-Add storage cost is not accumulated")
    if len(re.findall(COUNTER + r"\s*=", loop_clean, re.I)) != 1:
        raise AssertionError("StorageOperations has unexpected updates in the Add loop")
    assert_only_spans(
        growth,
        ((growth_copy.start(), growth_copy.end()),),
        "the capacity-growth branch must only add the prior element count",
    )
    counter_start = growth_close + 1 + counter_update.start()
    counter_end = growth_close + 1 + counter_update.end()
    assert_only_spans(
        loop_clean,
        (
            (count_match.start(), count_match.end()),
            (capacity_match.start(), capacity_match.end()),
            (add_matches[0].start(), add_matches[0].end()),
            (delta_matches[0].start(), delta_matches[0].end()),
            (growth_match.start(), growth_close + 1),
            (counter_start, counter_end),
        ),
        "the Add loop contains an unexpected statement or side effect",
    )

    output_tokens = re.compile(
        r"(?<![\w-])(?:Write-Output|WriteObject|return|break|continue)(?![\w-])",
        re.I,
    )
    if output_tokens.search(mask_comments_and_strings(process)):
        raise AssertionError("process must only buffer transformed values")
    if "|" in mask_comments_and_strings(process):
        raise AssertionError("process must not send values to a downstream pipeline")

    end_clean = remove_comments(end)
    final_count = re.search(
        rf"{COUNTER}\s*=\s*(?:\[\s*long\s*\]\s*)?{COUNTER}\s*"
        rf"\+\s*\${re.escape(buffer)}\.Count\b",
        end_clean,
        re.I,
    )
    materialize = re.search(
        rf"\${re.escape(buffer)}\.ToArray\s*\(\s*\)",
        end_clean,
        re.I,
    )
    if final_count is None or materialize is None or final_count.start() > materialize.start():
        raise AssertionError("final ToArray copies must be counted before materialization")
    if not is_top_level(end_clean, final_count.start()) or not is_top_level(end_clean, materialize.start()):
        raise AssertionError("final materialization must be unconditional")
    if len(re.findall(COUNTER + r"\s*=", end_clean, re.I)) != 1:
        raise AssertionError("StorageOperations has unexpected updates in end")
    if not re.search(
        rf"\${re.escape(buffer)}\.ToArray\s*\(\s*\)\s*$",
        end_clean,
        re.I,
    ):
        raise AssertionError("ToArray must be the end block's final output expression")
    assert_only_spans(
        end_clean,
        ((final_count.start(), final_count.end()), (materialize.start(), materialize.end())),
        "end must only count and emit the final materialized array",
    )

    buffer_methods = re.findall(
        rf"\${re.escape(buffer)}\.([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        remove_comments(body),
        re.I,
    )
    if [method.lower() for method in buffer_methods] != ["add", "toarray"]:
        raise AssertionError("the result buffer has an unexpected method call")

    export_source = source[function_close + 1:]
    if re.fullmatch(
        r"\s*Export-ModuleMember\s+-Function\s+Invoke-ProfiledPipeline\s*",
        remove_comments(export_source),
        re.I,
    ) is None:
        raise AssertionError("the module's public export changed")

    return PipelinePlan(
        parameters=parameters,
        buffer=buffer,
        transformed=transformed,
        item=item,
        count_before=count_before,
        capacity_before=capacity_before,
        storage_delta=storage_delta,
        final_materialization=True,
    )


class PipelineProfilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MODULE.read_text(encoding="utf-8")
        cls.plan = parse_plan(cls.source)

    def test_retains_public_contract_and_uses_list_buffer(self) -> None:
        self.assertEqual(
            self.plan.parameters,
            ("InputObject", "Transform", "OperationCounter"),
        )
        self.assertTrue(self.plan.final_materialization)

    def test_preserves_zero_one_many_and_transform_order(self) -> None:
        zero, zero_operations = self.plan.invoke([], lambda value: [value])
        one, one_operations = self.plan.invoke([42], lambda value: [value])
        many, many_operations = self.plan.invoke(
            [1, 2, 3],
            lambda value: [] if value == 2 else [f"{value}-a", f"{value}-b"],
        )
        self.assertEqual(zero, [])
        self.assertEqual(zero_operations, 0)
        self.assertEqual(one, [42])
        self.assertEqual(one_operations, 2)
        self.assertEqual(many, ["1-a", "1-b", "3-a", "3-b"])
        self.assertEqual(many_operations, 8)

    def test_buffers_until_transforms_finish(self) -> None:
        transformed: list[int] = []

        def transform(value: object) -> list[object]:
            self.assertEqual(transformed, list(range(1, int(value))))
            transformed.append(int(value))
            return [value]

        results, _ = self.plan.invoke([1, 2, 3], transform)
        self.assertEqual(results, [1, 2, 3])
        self.assertEqual(transformed, [1, 2, 3])

    def test_transform_errors_propagate_and_stop_later_inputs(self) -> None:
        failure = RuntimeError("transform failed at 2")
        visited: list[int] = []

        def transform(value: object) -> list[object]:
            visited.append(int(value))
            if value == 2:
                raise failure
            return [value]

        with self.assertRaises(RuntimeError) as raised:
            self.plan.invoke([1, 2, 3, 4], transform)
        self.assertIs(raised.exception, failure)
        self.assertEqual(visited, [1, 2])

    def test_counts_add_growth_and_materialization_writes(self) -> None:
        expected = {
            0: 0,
            1: 2,
            4: 8,
            5: 14,
            8: 20,
            9: 30,
            16: 44,
            17: 62,
            256: 764,
        }
        for item_count, operations in expected.items():
            with self.subTest(item_count=item_count):
                self.assertEqual(self.plan.storage_operations(item_count), operations)
        self.assertLessEqual(self.plan.storage_operations(256), 3 * 256)
        self.assertEqual(self.plan.storage_operations(17, initial=41), 103)

    def test_counts_emitted_values_instead_of_inputs(self) -> None:
        omitted, omitted_operations = self.plan.invoke([7], lambda _value: [])
        expanded, expanded_operations = self.plan.invoke(
            [7],
            lambda value: [value, int(value) + 1, int(value) + 2, int(value) + 3],
        )
        self.assertEqual(omitted, [])
        self.assertEqual(omitted_operations, 0)
        self.assertEqual(expanded, [7, 8, 9, 10])
        self.assertEqual(expanded_operations, 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
