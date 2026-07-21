"""Offline fallback checks for quorum-confirmed record selection."""

import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "quorum" / "reader.go"


def go_tokens(source):
    """Return the Go tokens needed for deterministic structural assertions."""
    tokens = []
    index = 0
    while index < len(source):
        character = source[index]
        if character.isspace():
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline == -1 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end == -1:
                raise ValueError("unterminated block comment")
            index = end + 2
            continue
        if character in {'"', "'", "`"}:
            quote = character
            start = index
            index += 1
            while index < len(source):
                if quote != "`" and source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    tokens.append(source[start:index])
                    break
                index += 1
            else:
                raise ValueError("unterminated string or rune literal")
            continue
        if character.isalpha() or character == "_":
            end = index + 1
            while end < len(source) and (
                source[end].isalnum() or source[end] == "_"
            ):
                end += 1
            tokens.append(source[index:end])
            index = end
            continue
        if character.isdigit():
            end = index + 1
            while end < len(source) and (
                source[end].isalnum() or source[end] in "._"
            ):
                end += 1
            tokens.append(source[index:end])
            index = end
            continue
        if source.startswith("...", index):
            tokens.append("...")
            index += 3
            continue
        if source[index:index + 2] in {
            ":=", "!=", "==", ">=", "<=", "++", "--", "&&", "||",
        }:
            tokens.append(source[index:index + 2])
            index += 2
            continue
        tokens.append(character)
        index += 1
    return tokens


def sequence_index(tokens, sequence, start=0):
    width = len(sequence)
    for index in range(start, len(tokens) - width + 1):
        if tokens[index:index + width] == sequence:
            return index
    return -1


def last_sequence_index(tokens, sequence):
    found = -1
    start = 0
    while True:
        index = sequence_index(tokens, sequence, start)
        if index < 0:
            return found
        found = index
        start = index + 1


def braced_body(tokens, opening):
    if tokens[opening] != "{":
        raise ValueError("body does not start with an opening brace")
    depth = 0
    for end in range(opening, len(tokens)):
        if tokens[end] == "{":
            depth += 1
        elif tokens[end] == "}":
            depth -= 1
            if depth == 0:
                return tokens[opening + 1:end]
    raise ValueError("unterminated block")


def function_body(tokens, name):
    for index, token in enumerate(tokens):
        if token != name or index == 0:
            continue
        if "func" not in tokens[max(0, index - 8):index]:
            continue
        opening = sequence_index(tokens, ["{"], index)
        if opening >= 0:
            return braced_body(tokens, opening)
    raise ValueError(f"function {name} not found")


def loop_body(tokens, header):
    start = sequence_index(tokens, header)
    if start < 0:
        raise AssertionError(f"missing loop: {' '.join(header)}")
    opening = sequence_index(tokens, ["{"], start + len(header))
    if opening < 0:
        raise AssertionError(f"missing loop body: {' '.join(header)}")
    return braced_body(tokens, opening)


def initialized_selection_flags(tokens):
    """Return flags that safely distinguish a first confirmed record."""
    flags = []
    for flag in ("haveSelection", "confirmed"):
        headers = (
            [
                "if", "!", flag, "||",
                "recordLess", "(", "selected", ",", "record", ")", "{",
            ],
            ["if", "!", flag, "{"],
        )
        for header in headers:
            start = sequence_index(tokens, header)
            if start < 0:
                continue
            body = braced_body(tokens, start + len(header) - 1)
            selects = sequence_index(body, ["selected", "=", "record"]) >= 0
            marks = sequence_index(body, [flag, "=", "true"]) >= 0
            if selects and marks:
                flags.append(flag)
                break
    return flags


class QuorumReaderSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tokens = go_tokens(SOURCE.read_text(encoding="utf-8"))
        cls.selector = function_body(cls.tokens, "selectConfirmed")
        cls.read = function_body(cls.tokens, "Read")

    def assertContains(self, tokens, sequence, message):
        self.assertGreaterEqual(sequence_index(tokens, sequence), 0, message)

    def test_source_is_lexically_balanced(self):
        pairs = {"(": ")", "[": "]", "{": "}"}
        closing = set(pairs.values())
        stack = []
        for token in self.tokens:
            if token in pairs:
                stack.append(pairs[token])
            elif token in closing:
                self.assertTrue(stack, f"unexpected closing delimiter {token!r}")
                self.assertEqual(stack.pop(), token)
        self.assertEqual([], stack, f"unclosed delimiters: {stack}")

    def test_selector_counts_only_successful_exact_records(self):
        read_loop = loop_body(
            self.selector,
            ["for", "_", ",", "result", ":=", "range", "reads"],
        )
        self.assertContains(
            read_loop,
            ["if", "result", ".", "err", "!=", "nil", "{", "continue", "}"],
            "failed reads must not count toward confirmation",
        )
        self.assertContains(
            read_loop,
            ["counts", "[", "result", ".", "record", "]", "++"],
            "counts must be keyed by the complete Record",
        )
        if sequence_index(
            read_loop, ["selected", "=", "result", ".", "record"],
        ) >= 0:
            confirmed_header = [
                "for", "record", ",", "count", ":=", "range", "counts",
            ]
            confirmed_start = sequence_index(self.selector, confirmed_header)
            confirmed_loop = loop_body(self.selector, confirmed_header)
            safe_reset = False
            for flag in initialized_selection_flags(confirmed_loop):
                prefix = self.selector[:confirmed_start]
                last_true = last_sequence_index(prefix, [flag, "=", "true"])
                last_false = max(
                    last_sequence_index(prefix, [flag, "=", "false"]),
                    last_sequence_index(prefix, [flag, ":=", "false"]),
                )
                default_false = (
                    last_true < 0
                    and sequence_index(prefix, ["var", flag, "bool"]) >= 0
                )
                if last_false > last_true or default_false:
                    safe_reset = True
            self.assertTrue(
                safe_reset,
                "a selection made from individual reads must be discarded before "
                "examining confirmed records",
            )

    def test_only_quorum_confirmed_records_compete_for_selection(self):
        confirmed_loop = loop_body(
            self.selector,
            ["for", "record", ",", "count", ":=", "range", "counts"],
        )
        quorum_guard = [
            "if", "count", "<", "quorum", "{", "continue", "}",
        ]
        guard_index = sequence_index(confirmed_loop, quorum_guard)
        self.assertGreaterEqual(guard_index, 0, "records below quorum must be skipped")
        self.assertContains(
            confirmed_loop[guard_index + len(quorum_guard):],
            ["recordLess", "(", "selected", ",", "record", ")"],
            "confirmed records must compete using the complete record order",
        )
        self.assertTrue(
            initialized_selection_flags(confirmed_loop),
            "the first confirmed record must initialize selection before comparisons",
        )

    def test_no_confirmed_record_returns_no_quorum(self):
        confirmed_loop = loop_body(
            self.selector,
            ["for", "record", ",", "count", ":=", "range", "counts"],
        )
        checks = []
        for flag in initialized_selection_flags(confirmed_loop):
            checks.append(
                sequence_index(
                    self.selector,
                    [
                        "if", "!", flag, "{",
                        "return", "Record", "{", "}", ",", "ErrNoQuorum",
                        "}",
                        "return", "selected", ",", "nil",
                    ],
                )
            )
        self.assertTrue(
            any(index >= 0 for index in checks),
            "the selector must reject reads with no confirmed exact record",
        )

    def test_read_repairs_only_successful_older_records_after_selection(self):
        select_call = [
            "selected", ",", "err", ":=",
            "selectConfirmed", "(", "reads", ",", "r", ".", "quorum", ")",
        ]
        repair_condition = [
            "if", "result", ".", "err", "==", "nil", "&&",
            "recordLess", "(", "result", ".", "record", ",", "selected", ")",
            "{",
            "_", "=", "r", ".", "replicas", "[", "result", ".", "index", "]",
            ".", "Repair", "(", "ctx", ",", "key", ",", "selected", ")",
            "}",
        ]
        selection = sequence_index(self.read, select_call)
        repair = sequence_index(self.read, repair_condition)
        self.assertGreaterEqual(selection, 0, "Read must use the confirmed selector")
        self.assertGreater(
            repair,
            selection,
            "read repair must occur only after successful selection",
        )

    def test_record_order_uses_version_then_value(self):
        order = function_body(self.tokens, "recordLess")
        self.assertContains(
            order,
            [
                "if", "left", ".", "Version", "!=", "right", ".", "Version", "{",
                "return", "left", ".", "Version", "<", "right", ".", "Version",
                "}",
                "return", "left", ".", "Value", "<", "right", ".", "Value",
            ],
            "records must order by version and then lexicographically by value",
        )


if __name__ == "__main__":
    unittest.main()
