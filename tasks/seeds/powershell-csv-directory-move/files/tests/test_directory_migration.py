#!/usr/bin/env python3
"""Hermetic contract tests for the PowerShell directory CSV migration."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "Move-DirectoryExport.ps1"

REQUIRED_COLUMNS = (
    "directory_id",
    "parent_directory_id",
    "relative_path",
    "owner_upn",
    "size_mib",
    "modified_local",
    "utc_offset_minutes",
    "is_deleted",
)
RECORD_COLUMNS = (
    "schema_version",
    "record_id",
    "parent_record_id",
    "relative_path",
    "owner_upn",
    "size_bytes",
    "modified_utc",
    "is_deleted",
)
REJECT_COLUMNS = ("source_file", "source_row", "error_code", "error_message")
REJECT_MESSAGE = "size_mib must be a non-negative invariant decimal"


def remove_comments(source: str) -> str:
    """Hide PowerShell comments while retaining executable strings and offsets."""
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


def split_top_level_arguments(arguments: str) -> tuple[str, ...]:
    """Split a small PowerShell argument list without evaluating it."""
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(arguments):
        char = arguments[index]
        if quote is not None:
            if char == "`" and quote == '"' and index + 1 < len(arguments):
                index += 1
            elif char == quote:
                if index + 1 < len(arguments) and arguments[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char in "([{" :
            depth += 1
        elif char in ")]}" :
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(arguments[start:index].strip())
            start = index + 1
        index += 1
    parts.append(arguments[start:].strip())
    return tuple(parts)


@dataclass(frozen=True)
class MigrationPlan:
    invariant_size_parse: bool


def normalize_expression(expression: str) -> str:
    return re.sub(r"\s+", "", expression).lower()


def is_invariant_culture(expression: str, code: str) -> bool:
    normalized = normalize_expression(expression)
    invariant_expressions = {
        "[cultureinfo]::invariantculture",
        "[globalization.cultureinfo]::invariantculture",
        "[globalization.numberformatinfo]::invariantinfo",
        "[system.globalization.cultureinfo]::invariantculture",
        "[system.globalization.numberformatinfo]::invariantinfo",
    }
    if normalized in invariant_expressions:
        return True
    if normalized != "$invariantculture":
        return False

    assignments = re.findall(
        r"\$InvariantCulture\s*=\s*(?P<value>[^\r\n]+)",
        code,
        re.IGNORECASE,
    )
    return (
        len(assignments) == 1
        and normalize_expression(assignments[0]) in invariant_expressions
    )


def parse_migration_plan(source: str) -> MigrationPlan:
    code = remove_comments(source)
    assignment = re.search(
        r"\$sizeMiB\s*=\s*\[decimal\]::Parse\((?P<arguments>.*?)\)"
        r"\s*\r?\n\s*if\s*\(\$sizeMiB\s*-lt\s*0\)",
        code,
        re.DOTALL | re.IGNORECASE,
    )
    if assignment is None:
        raise AssertionError("size_mib must be parsed once before its range check")
    if len(re.findall(r"\$sizeMiB\s*=", code, re.IGNORECASE)) != 1:
        raise AssertionError("size_mib must have one conversion assignment")
    arguments = split_top_level_arguments(assignment.group("arguments"))
    number_styles = {
        "[globalization.numberstyles]::number",
        "[system.globalization.numberstyles]::number",
    }
    invariant = False
    if len(arguments) == 2:
        # Decimal.Parse(string, IFormatProvider) uses NumberStyles.Number.
        invariant = is_invariant_culture(arguments[1], code)
    elif len(arguments) == 3:
        invariant = (
            normalize_expression(arguments[1]) in number_styles
            and is_invariant_culture(arguments[2], code)
        )
    return MigrationPlan(invariant_size_parse=invariant)


def csv_line(values: tuple[object, ...]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="", quoting=csv.QUOTE_ALL)
    writer.writerow(["" if value is None else str(value) for value in values])
    return output.getvalue()


def stable_id(directory_id: str) -> str:
    canonical = directory_id.strip().lower()
    digest = hashlib.sha256(f"directory-v2\n{canonical}".encode()).hexdigest()
    return "dir_" + digest[:32]


def parse_size(raw: str, *, plan: MigrationPlan, culture: str) -> Decimal:
    value = raw.strip()
    if not plan.invariant_size_parse and culture == "de-DE":
        # Decimal.Parse(string) with de-DE accepts '.' as a group separator.
        value = value.replace(".", "")
    return Decimal(value)


def fixture() -> dict[str, list[dict[str, str]]]:
    return {
        "z.csv": [
            {
                "directory_id": "B",
                "parent_directory_id": "A",
                "relative_path": "projects/alpha, archive",
                "owner_upn": "BOB@EXAMPLE.COM",
                "size_mib": "0.25",
                "modified_local": "2024-06-01 12:00:00",
                "utc_offset_minutes": "120",
                "is_deleted": "1",
            }
        ],
        "a.csv": [
            {
                "directory_id": "A",
                "parent_directory_id": "",
                "relative_path": "projects/alpha",
                "owner_upn": "ALICE@EXAMPLE.COM",
                "size_mib": "1.5",
                "modified_local": "2024-03-10 01:30:00",
                "utc_offset_minutes": "-420",
                "is_deleted": "false",
            },
            {
                "directory_id": "BAD",
                "parent_directory_id": "A",
                "relative_path": "projects/bad",
                "owner_upn": "bad@example.com",
                "size_mib": "not-a-number",
                "modified_local": "2024-03-10 01:30:00",
                "utc_offset_minutes": "0",
                "is_deleted": "false",
            },
        ],
    }


def migrate_with_contract_double(
    plan: MigrationPlan,
    *,
    culture: str,
    batch_size: int = 0,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Model the documented I/O boundary using only local fixture records."""
    records = [csv_line(RECORD_COLUMNS)]
    rejects = [csv_line(REJECT_COLUMNS)]
    audits: list[str] = []
    accepted = 0
    rejected = 0
    processed_in_batch = 0
    checkpoint_writes = 1

    files = fixture()
    for source_file in sorted(files, key=lambda value: value.encode("utf-8")):
        for row_index, row in enumerate(files[source_file]):
            if batch_size and processed_in_batch == batch_size:
                processed_in_batch = 0
                checkpoint_writes += 1
            source_row = row_index + 2
            source_csv = csv_line(tuple(row[column] for column in REQUIRED_COLUMNS))
            input_hash = hashlib.sha256(source_csv.encode()).hexdigest()
            try:
                size_mib = parse_size(row["size_mib"], plan=plan, culture=culture)
                if size_mib < 0:
                    raise InvalidOperation
                size_bytes = (size_mib * Decimal(1048576)).quantize(
                    Decimal(1), rounding=ROUND_HALF_UP
                )
            except InvalidOperation:
                rejects.append(
                    csv_line((source_file, source_row, "invalid_size_mib", REJECT_MESSAGE))
                )
                audits.append(
                    json.dumps(
                        {
                            "event": "rejected",
                            "source_file": source_file,
                            "source_row": source_row,
                            "error_code": "invalid_size_mib",
                            "input_sha256": input_hash,
                        },
                        separators=(",", ":"),
                    )
                )
                rejected += 1
            else:
                record_id = stable_id(row["directory_id"])
                parent_id = (
                    stable_id(row["parent_directory_id"])
                    if row["parent_directory_id"].strip()
                    else ""
                )
                local = datetime.strptime(row["modified_local"], "%Y-%m-%d %H:%M:%S")
                offset = timezone(timedelta(minutes=int(row["utc_offset_minutes"])))
                modified_utc = local.replace(tzinfo=offset).astimezone(timezone.utc)
                records.append(
                    csv_line(
                        (
                            "2",
                            record_id,
                            parent_id,
                            row["relative_path"].strip("/"),
                            row["owner_upn"].strip().lower(),
                            str(size_bytes),
                            modified_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                            "true" if row["is_deleted"].lower() in {"true", "1"} else "false",
                        )
                    )
                )
                audits.append(
                    json.dumps(
                        {
                            "event": "accepted",
                            "source_file": source_file,
                            "source_row": source_row,
                            "record_id": record_id,
                            "input_sha256": input_hash,
                        },
                        separators=(",", ":"),
                    )
                )
                accepted += 1
            processed_in_batch += 1
            checkpoint_writes += 1

    artifacts = {
        "records.v2.csv": ("\n".join(records) + "\n").encode(),
        "rejected-rows.csv": ("\n".join(rejects) + "\n").encode(),
        "audit.jsonl": ("\n".join(audits) + "\n").encode(),
    }
    checkpoint = {
        "accepted_rows": accepted,
        "rejected_rows": rejected,
        "complete": True,
        "writes": checkpoint_writes,
    }
    return artifacts, checkpoint


class DirectoryMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.code = remove_comments(cls.source)
        cls.plan = parse_migration_plan(cls.source)

    def test_schema_order_and_stable_identity_are_pinned(self) -> None:
        required_block = re.search(
            r"\$RequiredColumns\s*=\s*@\((?P<body>.*?)\)\s*\r?\n",
            self.code,
            re.DOTALL,
        )
        self.assertIsNotNone(required_block)
        self.assertEqual(
            tuple(re.findall(r"'([^']+)'", required_block.group("body"))),
            REQUIRED_COLUMNS,
        )
        self.assertIn("[Array]::Sort($inputFiles, [StringComparer]::Ordinal)", self.code)
        self.assertIn("$DirectoryId.Trim().ToLowerInvariant()", self.code)
        self.assertIn('("directory-v2`n" + $canonicalId)', self.code)
        self.assertEqual(stable_id("A"), "dir_d6ddb6cf99cfd077578661403096afb7")
        self.assertEqual(stable_id("B"), "dir_014f5ef67d2326992575b471dcb6a388")

    def test_rejects_and_input_bound_checkpoints_remain_deterministic(self) -> None:
        for token in (
            "input_fingerprint = $inputFingerprint",
            "if ([string]$state.input_fingerprint -ne $inputFingerprint)",
            "$state.next_file = $fileIndex",
            "$state.next_row = $rowIndex + 1",
            "$state.accepted_rows = [int]$state.accepted_rows + 1",
            "$state.rejected_rows = [int]$state.rejected_rows + 1",
            "$state.complete = $true",
            "$processedThisRun -ge $BatchSize",
            "invalid_size_mib = 'size_mib must be a non-negative invariant decimal'",
        ):
            self.assertIn(token, self.code)
        self.assertGreaterEqual(self.code.count("Write-Checkpoint -Path $CheckpointPath"), 5)

    def test_output_encoding_and_audit_fields_are_stable(self) -> None:
        self.assertIn("$Utf8NoBom = [Text.UTF8Encoding]::new($false)", self.code)
        self.assertIn("$Line + \"`n\"", self.code)
        self.assertIn("ConvertTo-StableCsvLine", self.code)
        self.assertIn("[pscustomobject][ordered]", self.code)
        self.assertIn("Get-StableAuditJson -Event ([ordered]@{", self.code)
        for forbidden in ("Get-Date", "DateTime]::Now", "DateTime]::UtcNow", "Start-Sleep"):
            self.assertNotIn(forbidden, self.code)
        self.assertNotRegex(self.code, r"(?i)Invoke-WebRequest|Invoke-RestMethod|Start-Process")

    def test_invariant_decimal_plan_matches_one_shot_and_resumed_contract(self) -> None:
        one_shot, checkpoint = migrate_with_contract_double(
            self.plan, culture="de-DE"
        )
        resumed, resumed_checkpoint = migrate_with_contract_double(
            self.plan, culture="de-DE", batch_size=1
        )
        fresh, _ = migrate_with_contract_double(self.plan, culture="en-US")

        self.assertTrue(self.plan.invariant_size_parse)
        self.assertEqual(one_shot, resumed)
        self.assertEqual(one_shot, fresh)
        self.assertGreater(resumed_checkpoint["writes"], checkpoint["writes"])
        self.assertEqual(checkpoint["accepted_rows"], 2)
        self.assertEqual(checkpoint["rejected_rows"], 1)
        self.assertTrue(checkpoint["complete"])

        rows = list(
            csv.DictReader(io.StringIO(one_shot["records.v2.csv"].decode()))
        )
        self.assertEqual([row["size_bytes"] for row in rows], ["1572864", "262144"])
        self.assertEqual(rows[1]["parent_record_id"], rows[0]["record_id"])
        rejected = list(
            csv.DictReader(io.StringIO(one_shot["rejected-rows.csv"].decode()))
        )
        self.assertEqual(
            rejected,
            [
                {
                    "source_file": "a.csv",
                    "source_row": "3",
                    "error_code": "invalid_size_mib",
                    "error_message": REJECT_MESSAGE,
                }
            ],
        )
        for payload in one_shot.values():
            self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
            self.assertTrue(payload.endswith(b"\n"))
            self.assertNotIn(b"\r", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
