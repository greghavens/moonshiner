import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "telemetry" / "middleware.go"
STABLE_IMPORT = "go.opentelemetry.io/otel/semconv/v1.26.0"
LEGACY_IMPORTS = {
    "go.opentelemetry.io/otel/semconv/v1.17.0",
    "go.opentelemetry.io/otel/semconv/v1.17.0/httpconv",
}


def go_tokens(source):
    """Return enough Go tokens for deterministic structural assertions."""
    tokens = []
    index = 0
    while index < len(source):
        char = source[index]
        if char.isspace():
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
        if char in {'"', "'", "`"}:
            quote = char
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
        if char.isalpha() or char == "_":
            end = index + 1
            while end < len(source) and (source[end].isalnum() or source[end] == "_"):
                end += 1
            tokens.append(source[index:end])
            index = end
            continue
        if char.isdigit():
            end = index + 1
            while end < len(source) and (source[end].isalnum() or source[end] in "._"):
                end += 1
            tokens.append(source[index:end])
            index = end
            continue
        if source.startswith("...", index):
            tokens.append("...")
            index += 3
            continue
        if source[index:index + 2] in {":=", "!=", "==", ">=", "<=", "++", "--"}:
            tokens.append(source[index:index + 2])
            index += 2
            continue
        tokens.append(char)
        index += 1
    return tokens


def imports_from(tokens):
    imports = []
    index = 0
    while index < len(tokens):
        if tokens[index] != "import":
            index += 1
            continue
        index += 1
        grouped = index < len(tokens) and tokens[index] == "("
        if grouped:
            index += 1
        while index < len(tokens) and (not grouped or tokens[index] != ")"):
            if tokens[index] == ";":
                index += 1
                continue
            alias = None
            if not tokens[index].startswith(('"', "`")):
                alias = tokens[index]
                index += 1
            if index >= len(tokens) or not tokens[index].startswith(('"', "`")):
                raise ValueError("malformed Go import declaration")
            imports.append((alias, tokens[index][1:-1]))
            index += 1
            if not grouped:
                break
        if grouped:
            if index >= len(tokens) or tokens[index] != ")":
                raise ValueError("unterminated Go import group")
            index += 1
    return imports


def function_body(tokens, name):
    for index, token in enumerate(tokens):
        if token != name or index == 0:
            continue
        if "func" not in tokens[max(0, index - 8):index]:
            continue
        opening = tokens.index("{", index)
        depth = 0
        for end in range(opening, len(tokens)):
            if tokens[end] == "{":
                depth += 1
            elif tokens[end] == "}":
                depth -= 1
                if depth == 0:
                    return tokens[opening + 1:end]
        raise ValueError(f"unterminated function {name}")
    raise ValueError(f"function {name} not found")


def sequence_index(tokens, sequence):
    width = len(sequence)
    for index in range(len(tokens) - width + 1):
        if tokens[index:index + width] == sequence:
            return index
    return -1


class SemconvMoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.tokens = go_tokens(cls.source)

    def test_source_is_lexically_balanced(self):
        pairs = {"(": ")", "[": "]", "{": "}"}
        closing = set(pairs.values())
        stack = []
        for token in self.tokens:
            if token in pairs:
                stack.append(pairs[token])
            elif token in closing:
                self.assertTrue(stack, f"unexpected closing delimiter {token!r}")
                self.assertEqual(stack.pop(), token, f"mismatched closing delimiter {token!r}")
        self.assertEqual([], stack, f"unclosed delimiters: {stack}")

    def test_middleware_imports_the_stable_generated_semconv_package(self):
        imports = imports_from(self.tokens)
        matching = [(alias, path) for alias, path in imports if path == STABLE_IMPORT]
        self.assertEqual(1, len(matching), f"expected one normal import of {STABLE_IMPORT!r}")
        alias = matching[0][0] or "semconv"
        self.assertNotIn(alias, {"_", "."}, "semconv must have an addressable package name")
        self.assertTrue(LEGACY_IMPORTS.isdisjoint(path for _, path in imports))
        self.assertFalse(
            any(path.startswith("go.opentelemetry.io/otel/semconv/") and path != STABLE_IMPORT
                for _, path in imports),
            f"another semantic-convention package is imported: {imports}",
        )

    def test_stable_generated_keys_are_used_with_the_existing_inputs(self):
        imports = imports_from(self.tokens)
        alias = next(alias or "semconv" for alias, path in imports if path == STABLE_IMPORT)
        body = function_body(self.tokens, "Instrument")
        expected_uses = {
            "HTTPRequestMethodKey": [alias, ".", "HTTPRequestMethodKey", ".", "String", "(", "req", ".", "Method", ")"],
            "HTTPRouteKey": [alias, ".", "HTTPRouteKey", ".", "String", "(", "route", ")"],
            "NetworkProtocolVersionKey": [alias, ".", "NetworkProtocolVersionKey", ".", "String", "(", "protocolVersion", "(", "req", ")", ")"],
            "URLSchemeKey": [alias, ".", "URLSchemeKey", ".", "String", "(", "requestScheme", "(", "req", ")", ")"],
            "ServerAddressKey": [alias, ".", "ServerAddressKey", ".", "String", "(", "m", ".", "serverName", ")"],
            "HTTPResponseStatusCodeKey": [alias, ".", "HTTPResponseStatusCodeKey", ".", "Int", "(", "status", ")"],
        }
        for key, use in expected_uses.items():
            self.assertEqual(
                1,
                sum(body[index:index + len(use)] == use for index in range(len(body) - len(use) + 1)),
                f"Instrument must use {alias}.{key} exactly once with the preserved input",
            )

        for legacy_key in (
            '"http.method"',
            '"http.flavor"',
            '"http.scheme"',
            '"http.status_code"',
            '"net.host.name"',
        ):
            self.assertNotIn(legacy_key, self.tokens, f"legacy attribute key {legacy_key} remains")

    def test_instrumentation_flow_and_result_contract_are_preserved(self):
        imports = imports_from(self.tokens)
        alias = next(alias or "semconv" for alias, path in imports if path == STABLE_IMPORT)
        body = function_body(self.tokens, "Instrument")
        required_in_order = [
            ["ctx", ",", "span", ":=", "m", ".", "tracer", ".", "Start", "(", "ctx", ",", "spanName", "(", "req", ".", "Method", ",", "route", ")", ")"],
            ["defer", "span", ".", "End", "(", ")"],
            [
                "if", "span", ".", "IsRecording", "(", ")", "{",
                "span", ".", "SetAttributes", "(",
                alias, ".", "HTTPRequestMethodKey", ".", "String", "(", "req", ".", "Method", ")", ",",
                alias, ".", "HTTPRouteKey", ".", "String", "(", "route", ")", ",",
                alias, ".", "NetworkProtocolVersionKey", ".", "String", "(", "protocolVersion", "(", "req", ")", ")", ",",
                alias, ".", "URLSchemeKey", ".", "String", "(", "requestScheme", "(", "req", ")", ")", ",",
                alias, ".", "ServerAddressKey", ".", "String", "(", "m", ".", "serverName", ")", ",",
                ")", "}",
            ],
            ["status", ",", "err", ":=", "next", "(", "ctx", ",", "req", ")"],
            ["if", "!", "span", ".", "IsRecording", "(", ")", "{", "return", "status", ",", "err", "}"],
            ["span", ".", "SetAttributes", "(", alias, ".", "HTTPResponseStatusCodeKey", ".", "Int", "(", "status", ")", ")"],
            ["code", ",", "description", ":=", "serverStatus", "(", "status", ")"],
            ["span", ".", "SetStatus", "(", "code", ",", "description", ")"],
            ["if", "err", "!=", "nil", "{", "span", ".", "SetStatus", "(", "codes", ".", "Error", ",", "err", ".", "Error", "(", ")", ")", "}"],
            ["return", "status", ",", "err"],
        ]
        cursor = 0
        for sequence in required_in_order:
            relative = sequence_index(body[cursor:], sequence)
            self.assertGreaterEqual(relative, 0, f"missing preserved flow: {' '.join(sequence)}")
            cursor += relative + len(sequence)

    def test_protocol_scheme_and_status_derivation_are_preserved(self):
        protocol = function_body(self.tokens, "protocolVersion")
        self.assertGreaterEqual(
            sequence_index(protocol, ["if", "req", ".", "ProtoMajor", "==", "0", "{", "return", '""', "}"]),
            0,
        )
        self.assertGreaterEqual(
            sequence_index(protocol, ["return", "fmt", ".", "Sprintf", "(", '"%d.%d"', ",", "req", ".", "ProtoMajor", ",", "req", ".", "ProtoMinor", ")"]),
            0,
        )

        scheme = function_body(self.tokens, "requestScheme")
        for sequence in (
            ["if", "req", ".", "URL", "!=", "nil", "&", "&", "req", ".", "URL", ".", "Scheme", "!=", '""', "{", "return", "req", ".", "URL", ".", "Scheme", "}"],
            ["if", "req", ".", "TLS", "!=", "nil", "{", "return", '"https"', "}"],
            ["return", '"http"'],
        ):
            self.assertGreaterEqual(sequence_index(scheme, sequence), 0, f"missing scheme behavior: {' '.join(sequence)}")

        status = function_body(self.tokens, "serverStatus")
        self.assertGreaterEqual(
            sequence_index(status, ["if", "status", ">=", "http", ".", "StatusInternalServerError", "{", "return", "codes", ".", "Error", ",", "fmt", ".", "Sprintf", "(", '"HTTP %d"', ",", "status", ")", "}"]),
            0,
        )
        self.assertGreaterEqual(
            sequence_index(status, ["return", "codes", ".", "Unset", ",", '""']),
            0,
        )

    def test_low_cardinality_span_name_contract_is_preserved(self):
        body = function_body(self.tokens, "spanName")
        self.assertGreaterEqual(
            sequence_index(body, ["if", "route", "==", '""', "{", "return", "method", "}"]),
            0,
        )
        self.assertGreaterEqual(
            sequence_index(body, ["return", "method", "+", '" "', "+", "route"]),
            0,
        )

    @unittest.skipUnless(shutil.which("go"), "Go toolchain is not installed")
    def test_go_suite_when_toolchain_is_available(self):
        result = subprocess.run(
            ["go", "test", "./..."],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
