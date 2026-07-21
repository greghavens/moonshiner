#!/usr/bin/env python3
"""Offline source-contract checks for encrypted-field version rotation."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "EncryptedFieldRotation" / "FieldRotationService.cs"


def mask_non_code(source: str) -> str:
    """Mask comments and literals while preserving offsets and newlines."""
    masked = list(source)
    index = 0
    state = "code"

    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if state == "code":
            if character == "/" and following == "/":
                masked[index] = masked[index + 1] = " "
                state = "line-comment"
                index += 2
                continue
            if character == "/" and following == "*":
                masked[index] = masked[index + 1] = " "
                state = "block-comment"
                index += 2
                continue
            if character == '"':
                masked[index] = " "
                state = "string"
            elif character == "'":
                masked[index] = " "
                state = "character"
        elif state == "line-comment":
            if character == "\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block-comment":
            if character == "*" and following == "/":
                masked[index] = masked[index + 1] = " "
                state = "code"
                index += 2
                continue
            if character != "\n":
                masked[index] = " "
        else:
            masked[index] = " "
            delimiter = '"' if state == "string" else "'"
            if character == "\\":
                if index + 1 < len(source):
                    if source[index + 1] != "\n":
                        masked[index + 1] = " "
                    index += 2
                    continue
            elif character == delimiter:
                state = "code"

        index += 1

    return "".join(masked)


def balanced_region(masked: str, opening: int, opener: str, closer: str) -> tuple[int, int]:
    depth = 0
    for index in range(opening, len(masked)):
        character = masked[index]
        if character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return opening + 1, index
    raise AssertionError(f"unbalanced {opener}{closer} region")


def rotate_method(source: str, masked: str) -> tuple[str, str, int]:
    signature = re.search(
        r"public\s+RotationResult\s+Rotate\s*\(\s*int\s+batchSize\s*=\s*100\s*\)\s*\{",
        masked,
    )
    if signature is None:
        raise AssertionError("Rotate must keep its public API and default batch size")

    opening = masked.find("{", signature.start())
    start, end = balanced_region(masked, opening, "{", "}")
    return source[start:end], masked[start:end], start


def split_arguments(source: str, masked: str, opening: int) -> tuple[list[str], int]:
    start, end = balanced_region(masked, opening, "(", ")")
    arguments: list[str] = []
    argument_start = start
    parens = brackets = braces = 0

    for index in range(start, end):
        character = masked[index]
        if character == "(":
            parens += 1
        elif character == ")":
            parens -= 1
        elif character == "[":
            brackets += 1
        elif character == "]":
            brackets -= 1
        elif character == "{":
            braces += 1
        elif character == "}":
            braces -= 1
        elif character == "," and parens == brackets == braces == 0:
            arguments.append(source[argument_start:index].strip())
            argument_start = index + 1

    arguments.append(source[argument_start:end].strip())
    return arguments, end


def calls(source: str, masked: str, receiver: str, method: str) -> list[tuple[list[str], int]]:
    pattern = re.compile(
        rf"\b{re.escape(receiver)}\s*\.\s*{re.escape(method)}\s*\("
    )
    found: list[tuple[list[str], int]] = []
    for match in pattern.finditer(masked):
        opening = masked.find("(", match.start())
        arguments, end = split_arguments(source, masked, opening)
        found.append((arguments, end))
    return found


def unwrap_parentheses(expression: str) -> str:
    expression = expression.strip()
    while expression.startswith("(") and expression.endswith(")"):
        masked = mask_non_code(expression)
        _, end = balanced_region(masked, 0, "(", ")")
        if end != len(expression) - 1:
            break
        expression = expression[1:-1].strip()
    return expression


def resolves_to_active_version(expression: str, prefix: str, seen: set[str] | None = None) -> bool:
    expression = unwrap_parentheses(expression)
    if re.fullmatch(r"_cipher\s*\.\s*ActiveKeyVersion", expression):
        return True

    if not re.fullmatch(r"[A-Za-z_]\w*", expression):
        return False

    name = expression
    if seen is None:
        seen = set()
    if name in seen:
        return False
    seen.add(name)

    assignment = re.compile(
        rf"(?:\b(?:var|string)\s+)?\b{re.escape(name)}\s*=\s*(?P<value>[^;]+);"
    )
    matches = list(assignment.finditer(mask_non_code(prefix)))
    if not matches:
        return False

    latest = matches[-1]
    value = prefix[latest.start("value"):latest.end("value")]
    return resolves_to_active_version(value, prefix[:latest.start()], seen)


def verify_rotation_contract() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    masked = mask_non_code(source)
    body, body_masked, _ = rotate_method(source, masked)

    decrypt_calls = calls(body, body_masked, "_cipher", "Decrypt")
    if len(decrypt_calls) != 1 or len(decrypt_calls[0][0]) != 3:
        raise AssertionError("Rotate must decrypt each legacy field once")
    decrypt_arguments = [re.sub(r"\s+", "", item) for item in decrypt_calls[0][0]]
    if decrypt_arguments != ["record.Id", "record.Ciphertext", "record.KeyVersion"]:
        raise AssertionError("legacy ciphertext must be decrypted with its stored key version")

    encrypt_calls = calls(body, body_masked, "_cipher", "Encrypt")
    if len(encrypt_calls) != 1 or len(encrypt_calls[0][0]) != 3:
        raise AssertionError("Rotate must create one replacement ciphertext")
    encrypt_arguments, encrypt_end = encrypt_calls[0]
    if not resolves_to_active_version(encrypt_arguments[2], body[:encrypt_end]):
        raise AssertionError("replacement ciphertext must use the active key")

    replace_calls = calls(body, body_masked, "_secrets", "TryReplace")
    if len(replace_calls) != 1 or len(replace_calls[0][0]) != 4:
        raise AssertionError("ciphertext and key version must be replaced atomically")
    replace_arguments, replace_end = replace_calls[0]
    normalized = [re.sub(r"\s+", "", item) for item in replace_arguments[:2]]
    if normalized != ["record.Id", "record.Revision"]:
        raise AssertionError("replacement must retain the record identity and revision guard")
    if not resolves_to_active_version(replace_arguments[3], body[:replace_end]):
        raise AssertionError("replacement metadata must persist the active key version")
    if re.fullmatch(r"record\s*\.\s*KeyVersion", replace_arguments[3].strip()):
        raise AssertionError("replacement metadata cannot retain the legacy key version")

    checkpoint_calls = calls(body, body_masked, "_checkpoints", "Save")
    if len(checkpoint_calls) != 2:
        raise AssertionError("Rotate must checkpoint active and successfully replaced rows")
    last_checkpoint = body_masked.rfind("_checkpoints")
    if replace_end >= last_checkpoint:
        raise AssertionError("a rotated row cannot be checkpointed before replacement")


def main() -> int:
    try:
        verify_rotation_contract()
    except (AssertionError, OSError) as error:
        print(f"FAIL encrypted-field rotation contract: {error}", file=sys.stderr)
        return 1

    print("PASS encrypted-field rotation contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
