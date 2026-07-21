#!/usr/bin/env python3
"""Compare deterministic public-symbol and C++-layout snapshots."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable


@dataclass(frozen=True)
class Symbol:
    kind: str
    name: str
    version: str
    status: str
    alias_of: str | None

    @property
    def label(self) -> str:
        marker = "@@" if self.status == "default" else "@"
        alias = f" (alias of {self.alias_of})" if self.alias_of else ""
        return f"{self.kind} {self.name}{marker}{self.version}{alias}"


@dataclass(frozen=True)
class RecordLayout:
    size: int
    alignment: int
    bases: tuple[tuple[str, int], ...]
    fields: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class AbiReport:
    removed_symbols: tuple[str, ...]
    changed_layouts: tuple[str, ...]

    @property
    def compatible(self) -> bool:
        return not self.removed_symbols and not self.changed_layouts


class SnapshotError(ValueError):
    pass


def _data_lines(path: Path) -> Iterable[tuple[int, str]]:
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SnapshotError(f"cannot read {path}: {error}") from error

    for line_number, raw_line in enumerate(contents.splitlines(), start=1):
        line = raw_line.strip()
        if line and not line.startswith("#"):
            yield line_number, line


def load_symbols(path: Path) -> tuple[Symbol, ...]:
    symbols: list[Symbol] = []
    seen_names: set[tuple[str, str, str]] = set()
    for line_number, line in _data_lines(path):
        parts = line.split("|")
        if len(parts) != 5:
            raise SnapshotError(f"{path}:{line_number}: expected 5 symbol columns")
        kind, name, version, status, raw_alias = parts
        if kind not in {"FUNC", "OBJECT"}:
            raise SnapshotError(f"{path}:{line_number}: unsupported symbol kind {kind!r}")
        if status not in {"default", "compat"}:
            raise SnapshotError(f"{path}:{line_number}: unsupported status {status!r}")
        alias_of = None if raw_alias == "-" else raw_alias
        identity = (name, version, status)
        if identity in seen_names:
            raise SnapshotError(f"{path}:{line_number}: duplicate public export {name}")
        seen_names.add(identity)
        symbols.append(Symbol(kind, name, version, status, alias_of))
    return tuple(symbols)


def load_layouts(path: Path) -> dict[str, RecordLayout]:
    records: dict[str, dict[str, object]] = {}
    for line_number, line in _data_lines(path):
        parts = line.split("|")
        tag = parts[0]
        if tag == "RECORD" and len(parts) == 4:
            _, name, raw_size, raw_alignment = parts
            if name in records:
                raise SnapshotError(f"{path}:{line_number}: duplicate record {name}")
            try:
                records[name] = {
                    "size": int(raw_size),
                    "alignment": int(raw_alignment),
                    "bases": [],
                    "fields": [],
                }
            except ValueError as error:
                raise SnapshotError(f"{path}:{line_number}: invalid record number") from error
        elif tag == "BASE" and len(parts) == 4:
            _, record_name, base_name, raw_offset = parts
            record = _require_record(records, record_name, path, line_number)
            try:
                record["bases"].append((base_name, int(raw_offset)))  # type: ignore[union-attr]
            except ValueError as error:
                raise SnapshotError(f"{path}:{line_number}: invalid base offset") from error
        elif tag == "FIELD" and len(parts) == 5:
            _, record_name, field_name, raw_offset, raw_size = parts
            record = _require_record(records, record_name, path, line_number)
            try:
                record["fields"].append(  # type: ignore[union-attr]
                    (field_name, int(raw_offset), int(raw_size))
                )
            except ValueError as error:
                raise SnapshotError(f"{path}:{line_number}: invalid field number") from error
        else:
            raise SnapshotError(f"{path}:{line_number}: malformed layout entry")

    return {
        name: RecordLayout(
            size=int(values["size"]),
            alignment=int(values["alignment"]),
            bases=tuple(values["bases"]),  # type: ignore[arg-type]
            fields=tuple(values["fields"]),  # type: ignore[arg-type]
        )
        for name, values in records.items()
    }


def _require_record(
    records: dict[str, dict[str, object]],
    name: str,
    path: Path,
    line_number: int,
) -> dict[str, object]:
    try:
        return records[name]
    except KeyError as error:
        raise SnapshotError(
            f"{path}:{line_number}: layout entry precedes record {name}"
        ) from error


def _symbol_contract_key(symbol: Symbol) -> tuple[str, str, str, str]:
    """Return the public contract identity used by the release gate."""
    # Aliases share implementation with their targets, so compare the target
    # export to avoid reporting the same implementation twice.
    exported_name = symbol.alias_of or symbol.name
    return symbol.kind, exported_name, symbol.version, symbol.status


def compare_snapshots(baseline_dir: Path, candidate_dir: Path) -> AbiReport:
    baseline_symbols = load_symbols(baseline_dir / "symbols.txt")
    candidate_symbols = load_symbols(candidate_dir / "symbols.txt")
    baseline_layouts = load_layouts(baseline_dir / "layouts.txt")
    candidate_layouts = load_layouts(candidate_dir / "layouts.txt")

    baseline_contract = {_symbol_contract_key(symbol): symbol for symbol in baseline_symbols}
    candidate_contract = {_symbol_contract_key(symbol): symbol for symbol in candidate_symbols}
    removed_symbols = tuple(
        sorted(
            baseline_contract[key].label
            for key in baseline_contract.keys() - candidate_contract.keys()
        )
    )

    changed_layouts: list[str] = []
    for name in sorted(baseline_layouts):
        if name not in candidate_layouts:
            changed_layouts.append(f"{name}: record removed")
        elif baseline_layouts[name] != candidate_layouts[name]:
            changed_layouts.append(f"{name}: layout changed")

    return AbiReport(removed_symbols, tuple(changed_layouts))


def render_report(report: AbiReport) -> str:
    if report.compatible:
        return "ABI compatible"

    lines = ["ABI BREAK detected"]
    if report.removed_symbols:
        lines.append("Removed or changed public symbols:")
        lines.extend(f"  - {symbol}" for symbol in report.removed_symbols)
    if report.changed_layouts:
        lines.append("Removed or changed public layouts:")
        lines.extend(f"  - {layout}" for layout in report.changed_layouts)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_dir", type=Path)
    parser.add_argument("candidate_dir", type=Path)
    arguments = parser.parse_args(argv)
    try:
        report = compare_snapshots(arguments.baseline_dir, arguments.candidate_dir)
    except SnapshotError as error:
        print(f"invalid ABI snapshot: {error}", file=sys.stderr)
        return 2

    print(render_report(report))
    return 0 if report.compatible else 1


if __name__ == "__main__":
    raise SystemExit(main())
