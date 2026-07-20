#!/usr/bin/env python3
"""Hermetic contract tests for the PowerShell Graph command migration."""

from __future__ import annotations

import json
import re
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "Get-TenantUserInventory.ps1"
CONTRACT_PATH = ROOT / "contracts" / "graph_command_generations.json"


class CommandNotAvailable(RuntimeError):
    pass


class GraphPageError(RuntimeError):
    pass


@dataclass(frozen=True)
class InvocationPlan:
    command: str
    parameters: tuple[str, ...]
    all_binding: str
    properties: tuple[str, ...]
    error_action: str | None
    output_mapping: tuple[tuple[str, str], ...]
    direct_pipeline: bool


def mask_comments_and_strings(source: str) -> str:
    """Keep source offsets while hiding comments and quoted strings."""
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
    """Remove line and block comments while preserving strings and newlines."""
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


def parse_parameters(invocation: str, masked_invocation: str) -> dict[str, tuple[str | None, str]]:
    pattern = re.compile(
        r"(?<![\w-])-(?P<name>[A-Za-z][A-Za-z0-9]*)"
        r"(?:\s*:\s*(?P<colon>\$(?:true|false)))?",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(masked_invocation))
    if matches:
        leading = re.sub(r"`\r?\n", "", masked_invocation[:matches[0].start()])
        if leading.strip():
            raise AssertionError("the invocation must bind parameters directly without splatting")
    parsed: dict[str, tuple[str | None, str]] = {}
    for offset, match in enumerate(matches):
        end = matches[offset + 1].start() if offset + 1 < len(matches) else len(invocation)
        value = re.sub(r"`\r?\n", "", invocation[match.end():end]).strip()
        name = match.group("name").lower()
        if name in parsed:
            raise AssertionError(f"parameter -{match.group('name')} is bound more than once")
        parsed[name] = (match.group("colon"), value)
    return parsed


def parse_literal_property_list(value: str) -> tuple[str, ...]:
    token_pattern = re.compile(
        r"(?:[A-Za-z][A-Za-z0-9]*|'(?:[^']|'')*'|\"(?:[^\"`]|`.)*\")"
    )
    parts = value.split(",")
    if not parts or any(not token_pattern.fullmatch(part.strip()) for part in parts):
        raise AssertionError("-Property must be a literal comma-separated property list")
    tokens = tuple(part.strip() for part in parts)
    return tuple(token[1:-1] if token[:1] in {"'", '"'} else token for token in tokens)


def parse_output_mapping(selector: str) -> tuple[tuple[str, str], ...]:
    selector = re.sub(r"^\s*-Property\s+", "", selector, count=1, flags=re.IGNORECASE)
    name_key = r"(?:Name|N|Label|L)"
    expression_key = r"(?:Expression|E)"
    object_id = r"(?:'ObjectId'|\"ObjectId\")"
    id_expression = r"\{\s*\$_\.Id\s*\}"
    calculated = re.compile(
        rf"@\{{\s*(?:"
        rf"{name_key}\s*=\s*{object_id}\s*;\s*{expression_key}\s*=\s*{id_expression}"
        rf"|{expression_key}\s*=\s*{id_expression}\s*;\s*{name_key}\s*=\s*{object_id}"
        rf")\s*\}}",
        re.IGNORECASE,
    )
    normalized, replacements = calculated.subn("ObjectId<-Id", selector)
    if replacements > 1:
        raise AssertionError("ObjectId is projected more than once")

    mapping: list[tuple[str, str]] = []
    for item in normalized.strip().split(","):
        item = item.strip()
        if not item:
            raise AssertionError("Select-Object contains an empty property")
        if item == "ObjectId<-Id":
            mapping.append(("ObjectId", "Id"))
        elif match := re.fullmatch(
            r"[A-Za-z][A-Za-z0-9]*|'[A-Za-z][A-Za-z0-9]*'|\"[A-Za-z][A-Za-z0-9]*\"",
            item,
        ):
            property_name = match.group(0).strip("'\"")
            mapping.append((property_name, property_name))
        else:
            raise AssertionError(f"unsupported Select-Object projection: {item!r}")
    return tuple(mapping)


def parse_script(source: str) -> InvocationPlan:
    masked = mask_comments_and_strings(source)
    command_pattern = re.compile(r"(?<![\w-])Get-(?:AzureAD|Mg)User(?![\w-])", re.IGNORECASE)
    commands = list(command_pattern.finditer(masked))
    if len(commands) != 1:
        raise AssertionError(f"expected one user retrieval command, found {len(commands)}")

    command_match = commands[0]
    command = source[command_match.start():command_match.end()]
    pipe_at = masked.find("|", command_match.end())
    if pipe_at < 0:
        raise AssertionError("the retrieval command must feed Select-Object")
    if masked.find("|", pipe_at + 1) >= 0:
        raise AssertionError("the stable projection must be the final pipeline stage")

    invocation = remove_comments(source[command_match.end():pipe_at])
    invocation_masked = masked[command_match.end():pipe_at]
    parameters = parse_parameters(invocation, invocation_masked)

    all_colon, all_value = parameters.get("all", (None, "<missing>"))
    if all_colon and all_colon.lower() == "$true" and not all_value:
        all_binding = "switch"
    elif all_colon is None and not all_value:
        all_binding = "switch"
    elif all_colon is None and all_value.lower() == "$true":
        all_binding = "boolean-true"
    else:
        all_binding = "disabled-or-invalid"

    property_value = parameters.get("property", (None, ""))[1]
    properties = parse_literal_property_list(property_value) if property_value else ()
    error_action = parameters.get("erroraction", (None, ""))[1] or None

    after_pipe = remove_comments(source[pipe_at + 1:])
    select_match = re.match(r"\s*Select-Object\b", after_pipe, re.IGNORECASE)
    if not select_match:
        raise AssertionError("Get-MgUser must feed Select-Object directly")
    selector = after_pipe[select_match.end():].strip()
    output_mapping = parse_output_mapping(selector)

    line_start = masked.rfind("\n", 0, command_match.start()) + 1
    prefix = masked[line_start:command_match.start()].strip()
    preamble = remove_comments(source[:command_match.start()])
    expected_preamble = re.compile(
        r"\s*\[\s*CmdletBinding\s*\(\s*\)\s*\]\s*"
        r"param\s*\(\s*\)\s*"
        r"Set-StrictMode\s+-Version\s+Latest\s*",
        re.IGNORECASE,
    )
    direct_pipeline = not prefix and not re.search(
        r"(?<![\w-])(?:try|catch|return|Write-Output)(?![\w-])", masked, re.IGNORECASE
    ) and expected_preamble.fullmatch(preamble) is not None
    return InvocationPlan(
        command=command,
        parameters=tuple(parameters),
        all_binding=all_binding,
        properties=properties,
        error_action=error_action,
        output_mapping=output_mapping,
        direct_pipeline=direct_pipeline,
    )


class CurrentGraphUsersDouble:
    """Model only the locally documented command behavior used by this script."""

    pages = (
        (
            {
                "Id": "u-001",
                "DisplayName": "Ada Lovelace",
                "UserPrincipalName": "ada@example.test",
                "AccountEnabled": True,
                "Department": "Research",
            },
        ),
        (
            {
                "Id": "u-002",
                "DisplayName": "Grace Hopper",
                "UserPrincipalName": "grace@example.test",
                "AccountEnabled": False,
                "Department": "Operations",
            },
            {
                "Id": "u-003",
                "DisplayName": "Margaret Hamilton",
                "UserPrincipalName": "margaret@example.test",
                "AccountEnabled": True,
                "Department": "Engineering",
            },
        ),
    )

    def __init__(self, contract: dict, failure: GraphPageError | None = None) -> None:
        self.contract = contract
        self.failure = failure
        self.nonterminating_errors: list[GraphPageError] = []

    def invoke(self, plan: InvocationPlan) -> Iterator[dict[str, object]]:
        current = self.contract["current_generation"]
        if plan.command.lower() != current["command"].lower():
            raise CommandNotAvailable(f"{plan.command} is not installed")

        page_count = len(self.pages) if plan.all_binding == "switch" else 1
        for page_number, page in enumerate(self.pages[:page_count], start=1):
            if page_number == 2 and self.failure is not None:
                if (plan.error_action or "").lower() == "stop":
                    raise self.failure
                self.nonterminating_errors.append(self.failure)
                continue
            for record in page:
                selected = {name: record.get(name) for name in plan.properties}
                yield {output: selected.get(source) for output, source in plan.output_mapping}


class GraphModuleMoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.plan = parse_script(cls.source)

    def test_uses_current_command_and_switch_binding(self) -> None:
        current = self.contract["current_generation"]
        self.assertEqual(self.plan.command.lower(), current["command"].lower())
        self.assertEqual(
            set(self.plan.parameters),
            {
                current["all_parameter"]["name"].lower(),
                current["property_parameter"].lower(),
                current["error_parameter"]["name"].lower(),
            },
        )
        self.assertEqual(self.plan.all_binding, current["all_parameter"]["binding"])

    def test_explicit_property_selection_matches_current_contract(self) -> None:
        required = tuple(self.contract["current_generation"]["required_properties"])
        self.assertEqual(len(self.plan.properties), len(required))
        self.assertEqual(set(self.plan.properties), set(required))

    def test_all_pages_keep_the_stable_projection_and_order(self) -> None:
        stable = self.contract["stable_script_contract"]
        expected_mapping = tuple(stable["source_mapping"].items())
        self.assertEqual(self.plan.output_mapping, expected_mapping)
        self.assertTrue(self.plan.direct_pipeline)

        rows = list(CurrentGraphUsersDouble(self.contract).invoke(self.plan))
        self.assertEqual(rows, [
            {
                "ObjectId": "u-001",
                "DisplayName": "Ada Lovelace",
                "UserPrincipalName": "ada@example.test",
                "AccountEnabled": True,
            },
            {
                "ObjectId": "u-002",
                "DisplayName": "Grace Hopper",
                "UserPrincipalName": "grace@example.test",
                "AccountEnabled": False,
            },
            {
                "ObjectId": "u-003",
                "DisplayName": "Margaret Hamilton",
                "UserPrincipalName": "margaret@example.test",
                "AccountEnabled": True,
            },
        ])
        self.assertEqual([tuple(row) for row in rows], [tuple(stable["output_properties"])] * 3)

    def test_results_stream_before_the_original_terminating_error(self) -> None:
        current = self.contract["current_generation"]
        self.assertEqual(
            (self.plan.error_action or "").lower(),
            current["error_parameter"]["terminating_value"].lower(),
        )
        failure = GraphPageError("page two failed")
        stream = iter(CurrentGraphUsersDouble(self.contract, failure=failure).invoke(self.plan))
        self.assertEqual(next(stream)["ObjectId"], "u-001")
        with self.assertRaises(GraphPageError) as raised:
            next(stream)
        self.assertIs(raised.exception, failure)

    def test_protected_document_pins_both_command_generations(self) -> None:
        self.assertEqual(self.contract["legacy_generation"]["all_parameter"]["binding"], "boolean")
        self.assertEqual(self.contract["current_generation"]["module"], "Microsoft.Graph.Users")
        self.assertEqual(self.contract["stable_script_contract"]["delivery"], "pipeline-stream")
        self.assertEqual(self.contract["stable_script_contract"]["errors"], "terminating-unwrapped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
