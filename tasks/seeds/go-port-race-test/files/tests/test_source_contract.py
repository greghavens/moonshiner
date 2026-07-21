"""Offline fallback checks for serving on the injected listener."""

import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "internal" / "testserver" / "server.go"


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
            ":=", "!=", "==", ">=", "<=", "++", "--", "&&", "||", "<-",
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


def matching_delimiter(tokens, opening):
    pairs = {"(": ")", "[": "]", "{": "}"}
    expected = pairs[tokens[opening]]
    depth = 0
    for index in range(opening, len(tokens)):
        if tokens[index] == tokens[opening]:
            depth += 1
        elif tokens[index] == expected:
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"unterminated {tokens[opening]!r} block")


def braced_body(tokens, opening):
    return tokens[opening + 1:matching_delimiter(tokens, opening)]


def function_declaration(tokens, name):
    for index in range(len(tokens) - 1):
        if tokens[index:index + 2] != ["func", name]:
            continue
        parameters = sequence_index(tokens, ["("], index + 2)
        if parameters < 0:
            break
        parameters_end = matching_delimiter(tokens, parameters)
        opening = sequence_index(tokens, ["{"], parameters_end + 1)
        if opening < 0:
            break
        return (
            tokens[parameters + 1:parameters_end],
            tokens[parameters_end + 1:opening],
            braced_body(tokens, opening),
        )
    raise ValueError(f"function {name} not found")


def method_declaration(tokens, name):
    for index, token in enumerate(tokens):
        if token != "func" or index + 1 >= len(tokens) or tokens[index + 1] != "(":
            continue
        receiver_end = matching_delimiter(tokens, index + 1)
        if receiver_end + 1 >= len(tokens) or tokens[receiver_end + 1] != name:
            continue
        parameters = receiver_end + 2
        if parameters >= len(tokens) or tokens[parameters] != "(":
            continue
        parameters_end = matching_delimiter(tokens, parameters)
        opening = sequence_index(tokens, ["{"], parameters_end + 1)
        if opening < 0:
            break
        return (
            tokens[index + 2:receiver_end],
            tokens[parameters + 1:parameters_end],
            tokens[parameters_end + 1:opening],
            braced_body(tokens, opening),
        )
    raise ValueError(f"method {name} not found")


def parameter_before_type(parameters, type_tokens):
    position = sequence_index(parameters, type_tokens)
    if position <= 0:
        raise ValueError(f"missing named parameter of type {'.'.join(type_tokens)}")
    name = parameters[position - 1]
    if not (name[0].isalpha() or name[0] == "_"):
        raise ValueError(f"invalid parameter name {name!r}")
    return name


def aliases_from(body, root):
    aliases = {root}
    expression_continuations = {
        ".", "(", "[", "+", "-", "*", "/", "%", "&&", "||", "==", "!=",
        "<", ">", "<=", ">=", ",",
    }
    changed = True
    while changed:
        changed = False
        for index in range(len(body) - 2):
            if body[index + 1] not in {":=", "="}:
                continue
            continues = (
                index + 3 < len(body)
                and body[index + 3] in expression_continuations
            )
            if (
                body[index + 2] in aliases
                and not continues
                and body[index] not in aliases
            ):
                aliases.add(body[index])
                changed = True
        for index in range(len(body) - 3):
            if body[index] != "var" or body[index + 2] != "=":
                continue
            continues = (
                index + 4 < len(body)
                and body[index + 4] in expression_continuations
            )
            if (
                body[index + 3] in aliases
                and not continues
                and body[index + 1] not in aliases
            ):
                aliases.add(body[index + 1])
                changed = True
    return aliases


def struct_literal_body(tokens, type_name):
    for index in range(len(tokens) - 1):
        if tokens[index:index + 2] == [type_name, "{"]:
            return braced_body(tokens, index + 1)
    raise ValueError(f"struct literal {type_name} not found")


class InjectedListenerSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tokens = go_tokens(SOURCE.read_text(encoding="utf-8"))
        cls.parameters, cls.results, cls.start = function_declaration(
            cls.tokens, "Start"
        )
        cls.listener = parameter_before_type(
            cls.parameters, ["net", ".", "Listener"]
        )
        cls.handler = parameter_before_type(
            cls.parameters, ["http", ".", "Handler"]
        )
        cls.listener_aliases = aliases_from(cls.start, cls.listener)

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

    def test_public_api_is_preserved(self):
        self.assertEqual(
            ["*", "Server", ",", "error"],
            self.results[1:-1] if self.results[:1] == ["("] else self.results,
            "Start must still return (*Server, error)",
        )
        for name, result in (("Addr", ["string"]), ("Close", ["error"])):
            receiver, parameters, results, _ = method_declaration(self.tokens, name)
            self.assertEqual([], parameters, f"{name} must take no arguments")
            self.assertEqual(result, results, f"{name} result type changed")
            self.assertIn("*", receiver, f"{name} must retain a pointer receiver")
            self.assertIn("Server", receiver, f"{name} receiver must remain Server")

    def test_start_keeps_the_injected_listener_reservation(self):
        for alias in self.listener_aliases:
            self.assertEqual(
                -1,
                sequence_index(self.start, [alias, ".", "Close", "("]),
                "Start must not close the injected listener",
            )
        for listen_name in ("Listen", "ListenTCP", "ListenUnix"):
            self.assertEqual(
                -1,
                sequence_index(self.start, ["net", ".", listen_name, "("]),
                "Start must not replace the injected listener with a new bind",
            )

    def test_start_reports_the_exact_injected_address(self):
        address_expressions = [
            [alias, ".", "Addr", "(", ")", ".", "String", "(", ")"]
            for alias in self.listener_aliases
        ]
        address_variables = set()
        for index in range(len(self.start) - 2):
            if self.start[index + 1] not in {":=", "="}:
                continue
            if any(
                self.start[index + 2:index + 2 + len(expression)] == expression
                for expression in address_expressions
            ):
                address_variables.add(self.start[index])

        literal = struct_literal_body(self.start, "Server")
        field = sequence_index(literal, ["address", ":"])
        self.assertGreaterEqual(field, 0, "the returned Server must retain its address")
        field_value = literal[field + 2:]
        direct = any(sequence_index(field_value, expression) == 0
                     for expression in address_expressions)
        indirect = bool(field_value and field_value[0] in address_variables)
        self.assertTrue(
            direct or indirect,
            "Server.address must come from the injected listener's exact Addr().String()",
        )

    def test_server_uses_the_handler_and_serves_asynchronously_on_listener(self):
        literal = struct_literal_body(self.start, "Server")
        self.assertGreaterEqual(
            sequence_index(literal, ["Handler", ":", self.handler]),
            0,
            "the supplied handler must remain attached to the HTTP server",
        )

        goroutine = sequence_index(self.start, ["go", "func", "(", ")", "{"])
        self.assertGreaterEqual(goroutine, 0, "Start must launch serving asynchronously")
        body = braced_body(self.start, goroutine + 4)
        serve_arguments = []
        for index in range(len(body) - 4):
            if body[index:index + 3] == [".", "Serve", "("]:
                closing = matching_delimiter(body, index + 2)
                serve_arguments.append(body[index + 3:closing])
        self.assertTrue(
            any(arguments == [alias] for arguments in serve_arguments
                for alias in self.listener_aliases),
            "the HTTP server must Serve the listener supplied to Start",
        )


if __name__ == "__main__":
    unittest.main()
