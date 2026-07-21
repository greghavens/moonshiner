#!/usr/bin/env python3
"""Check the linked production FDE without depending on absolute addresses."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def output(*argv: str) -> str:
    return subprocess.run(argv, check=True, text=True,
                          stdout=subprocess.PIPE).stdout


def nested_return_pcs(runner: Path) -> list[int]:
    disassembly = output("objdump", "-d", "--no-show-raw-insn", str(runner))
    header = re.search(
        r"^[0-9a-fA-F]+ <accumulate_samples>:\s*$", disassembly, re.MULTILINE
    )
    if header is None:
        raise SystemExit("cannot disassemble accumulate_samples")
    next_header = re.search(
        r"^[0-9a-fA-F]+ <[^>]+>:\s*$", disassembly[header.end():], re.MULTILINE
    )
    end = header.end() + next_header.start() if next_header else len(disassembly)
    instructions = re.findall(
        r"^\s*([0-9a-fA-F]+):\s+(\S+)(?:\s+(.*))?$",
        disassembly[header.end():end],
        re.MULTILINE,
    )
    calls = [
        index for index, (_, mnemonic, operands) in enumerate(instructions)
        if mnemonic in {"call", "callq"}
    ]
    if not calls or any(index + 1 >= len(instructions) for index in calls):
        raise SystemExit("cannot identify the nested call return boundary")
    return [int(instructions[index + 1][0], 16) for index in calls]


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_unwind.py RUNNER")
    runner = Path(sys.argv[1])
    symbols = output("nm", "-n", "--defined-only", str(runner))
    match = re.search(
        r"^([0-9a-fA-F]+)\s+[Tt]\s+accumulate_samples$", symbols, re.MULTILINE
    )
    if match is None:
        raise SystemExit("missing accumulate_samples symbol")
    start = int(match.group(1), 16)

    frames = output("readelf", "--debug-dump=frames-interp", str(runner))
    fde = None
    for block in re.split(
        r"\n(?=[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+FDE\s+cie=)",
        frames,
    ):
        header = re.search(
            r"FDE\s+cie=.*?pc=([0-9a-fA-F]+)\.\.([0-9a-fA-F]+)", block
        )
        if header and int(header.group(1), 16) <= start < int(header.group(2), 16):
            fde = block
            break
    if fde is None:
        raise SystemExit("no FDE covers accumulate_samples")

    return_pcs = nested_return_pcs(runner)
    table = fde.splitlines()
    header_index = next(
        (index for index, line in enumerate(table)
         if re.match(r"^\s*LOC\s+CFA(?:\s|$)", line)),
        None,
    )
    if header_index is None:
        raise SystemExit("missing interpreted rows for accumulate_samples")
    columns = table[header_index].split()
    rows = []
    for line in table[header_index + 1:]:
        fields = line.split()
        if len(fields) == len(columns) and re.fullmatch(r"[0-9a-fA-F]+", fields[0]):
            rows.append((int(fields[0], 16), dict(zip(columns, fields))))
    required = {
        "rbp-based canonical frame": ("CFA", "rbp+16"),
        "saved rbp": ("rbp", "c-16"),
        "saved rbx": ("rbx", "c-24"),
        "saved r12": ("r12", "c-32"),
        "saved r13": ("r13", "c-40"),
        "saved r14": ("r14", "c-48"),
        "saved r15": ("r15", "c-56"),
    }
    for return_pc in return_pcs:
        active = [state for address, state in rows if address <= return_pc]
        if not active:
            raise SystemExit("no unwind row covers a nested call boundary")
        state = active[-1]
        missing = [
            label for label, (register, value) in required.items()
            if state.get(register) != value
        ]
        if missing:
            raise SystemExit("incomplete unwind state at a nested call boundary: " +
                             ", ".join(missing))
    print("unwind metadata checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
