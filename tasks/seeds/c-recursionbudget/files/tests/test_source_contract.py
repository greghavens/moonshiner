#!/usr/bin/env python3
from pathlib import Path
import re
import sys


source = Path("src/tree_decoder.c").read_text(encoding="utf-8")
errors = []


def directly_recurses(function_name):
    definition = re.search(
        rf"\b{re.escape(function_name)}\s*\([^;{{}}]*\)\s*\{{", source,
        re.DOTALL,
    )
    if definition is None:
        return False

    depth = 1
    index = definition.end()
    while index < len(source) and depth != 0:
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
        index += 1
    body = source[definition.end():index - 1]
    return re.search(rf"\b{re.escape(function_name)}\s*\(", body) is not None


if directly_recurses("decode_node_recursive"):
    errors.append("the decoder still contains its recursive decode helper")
if re.search(r"\[\s*TREE_DECODE_STACK_LIMIT\s*\]", source) is None:
    errors.append("the decoder does not use a fixed-size bounded frame stack")
if "TREE_DECODE_WORK_LIMIT" not in source:
    errors.append("the decoder does not use the published work limit")

if errors:
    for error in errors:
        print(f"source contract: {error}", file=sys.stderr)
    raise SystemExit(1)

print("source contract passed")
