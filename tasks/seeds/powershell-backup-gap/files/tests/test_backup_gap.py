#!/usr/bin/env python3
"""Hermetic contract tests for the PowerShell backup-gap repair.

The seed targets a focused PowerShell data-flow bug, but the corpus validator does
not provide a PowerShell runtime.  These tests inspect executable PowerShell (with
comments and strings masked where appropriate), validate the frozen evidence, and
pin the preflight/recovery/audit contract without invoking external commands.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "Repair-BackupGap.ps1"
FIXTURE_ROOT = ROOT / "fixtures"
MANIFEST_PATH = FIXTURE_ROOT / "incident-manifest.json"
TRANSCRIPT_PATH = FIXTURE_ROOT / "incident-transcript.log"


def mask_comments_and_strings(source: str) -> str:
    """Preserve offsets while hiding PowerShell comments and quoted strings."""
    masked = list(source)
    index = 0
    quote: str | None = None
    while index < len(source):
        char = source[index]
        if quote is not None:
            if char not in "\r\n":
                masked[index] = " "
            if char == "`" and quote == '"' and index + 1 < len(source):
                index += 1
                if source[index] not in "\r\n":
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


def function_body(source: str, name: str) -> str:
    """Return one named PowerShell function body using balanced braces."""
    masked = mask_comments_and_strings(source)
    matches = list(re.finditer(rf"(?im)^\s*function\s+{re.escape(name)}\s*\{{", masked))
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one {name} function, found {len(matches)}")

    opening = masked.find("{", matches[0].start(), matches[0].end())
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise AssertionError(f"unterminated {name} function")


def assert_patterns_in_order(
    test: unittest.TestCase,
    source: str,
    patterns: tuple[str, ...],
    message: str,
) -> None:
    cursor = 0
    for pattern in patterns:
        match = re.search(pattern, source[cursor:], re.IGNORECASE | re.MULTILINE | re.DOTALL)
        test.assertIsNotNone(match, f"{message}: missing /{pattern}/")
        assert match is not None
        cursor += match.end()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BackupGapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT_PATH.read_text(encoding="utf-8")
        cls.evidence_body = function_body(cls.source, "Get-BackupTranscriptEvidence")
        cls.repair_body = function_body(cls.source, "Repair-BackupGap")
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.transcript = TRANSCRIPT_PATH.read_text(encoding="utf-8").splitlines()

    def test_frozen_incident_evidence_is_internally_consistent(self) -> None:
        exit_records = [line for line in self.transcript if line.startswith("NATIVE_EXIT_CODE=")]
        self.assertEqual(exit_records, ["NATIVE_EXIT_CODE=23"])
        transcript_skipped = sorted(
            line.split("|", 2)[1]
            for line in self.transcript
            if line.startswith("SKIPPED|")
        )
        manifest_skipped = sorted(
            entry["path"] for entry in self.manifest["files"] if entry["state"] == "skipped"
        )
        self.assertEqual(self.manifest["status"], "partial_failure")
        self.assertEqual(self.manifest["native_exit_code"], 23)
        self.assertEqual(transcript_skipped, manifest_skipped)
        self.assertEqual(transcript_skipped, ["docs/quarterly report.txt", "keys/public.txt"])

        for entry in self.manifest["files"]:
            source = FIXTURE_ROOT / "source" / entry["path"]
            self.assertTrue(source.is_file(), f"missing source fixture {entry['path']}")
            self.assertEqual(sha256(source), entry["sha256"])
            destination = FIXTURE_ROOT / "partial-backup" / entry["path"]
            self.assertEqual(destination.is_file(), entry["state"] == "copied")
            if destination.is_file():
                self.assertEqual(sha256(destination), entry["sha256"])

    def test_transcript_exit_code_is_captured_once_and_never_masked(self) -> None:
        executable = mask_comments_and_strings(self.evidence_body)
        capture = re.search(
            r"(?im)^\s*(?:\[int\]\s*)?\$(?P<variable>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
            r"\[int\]\s*\$exitCodeMatches\s*\[\s*0\s*\]\s*\.Matches\s*"
            r"\[\s*0\s*\]\s*\.Groups\s*\[\s*1\s*\]\s*\.Value\s*$",
            executable,
        )
        self.assertIsNotNone(capture, "the transcript capture must be the native-code source")
        assert capture is not None
        variable = capture.group("variable")
        assignments = re.findall(
            rf"(?im)^\s*(?:\[[^\]\r\n]+\]\s*)?\${re.escape(variable)}\s*=",
            executable,
        )
        self.assertEqual(
            len(assignments),
            1,
            "the recorded transcript code is reassigned after capture",
        )
        self.assertRegex(
            executable,
            rf"(?im)^\s*NativeExitCode\s*=\s*\${re.escape(variable)}\s*$",
        )
        self.assertNotRegex(
            executable,
            r"(?<![A-Za-z0-9_])\$\?(?![A-Za-z0-9_])|\$LASTEXITCODE\b",
            "live command status must not replace frozen transcript evidence",
        )
        self.assertIn("^NATIVE_EXIT_CODE=([0-9]+)$", self.evidence_body)
        self.assertIn("^SKIPPED\\|([^|]+)\\|[^|]+$", self.evidence_body)

    def test_all_evidence_agreement_checks_precede_backup_mutation(self) -> None:
        first_copy = self.repair_body.find("[IO.File]::Copy")
        self.assertGreater(first_copy, 0, "recovery must perform a verified file copy")
        preflight = self.repair_body[:first_copy]
        assert_patterns_in_order(
            self,
            preflight,
            (
                r"\$manifest\s*=\s*Get-Content\b.*?ConvertFrom-Json",
                r"\$evidence\s*=\s*Get-BackupTranscriptEvidence\b",
                r"\$manifest\.status\s+-ne\s+['\"]partial_failure['\"]",
                r"\[int\]\s*\$manifest\.native_exit_code\s+-ne\s+\$evidence\.NativeExitCode",
                r"\$evidence\.NativeExitCode\s+-eq\s+0",
                r"Compare-Object\s+-ReferenceObject\s+\$manifestSkipped\s+"
                r"-DifferenceObject\s+\$evidence\.SkippedPaths",
                r"\$evidenceDifference\.Count\s+-ne\s+0",
            ),
            "incident evidence must agree before mutation",
        )

    def test_only_missing_skipped_files_enter_the_resume_plan(self) -> None:
        first_copy = self.repair_body.find("[IO.File]::Copy")
        preflight = self.repair_body[:first_copy]
        assert_patterns_in_order(
            self,
            preflight,
            (
                r"Test-Path\s+-LiteralPath\s+\$destinationPath\s+-PathType\s+Leaf",
                r"Get-BackupFileHash\s+-LiteralPath\s+\$destinationPath",
                r"\$destinationHash\s+-ne\s+\$sourceHash",
                r"throw\s+['\"]Refusing to overwrite conflicting backup file:",
                r"\$preserved\.Add\(",
                r"continue",
                r"\$entry\.state\s+-ne\s+['\"]skipped['\"]",
                r"throw\s+['\"]Manifest says copied but backup file is missing:",
                r"\$resumePlan\.Add\(",
            ),
            "resume planning must preserve verified files and reject conflicts/non-skipped gaps",
        )
        self.assertLess(
            preflight.find("Refusing to overwrite conflicting backup file:"),
            preflight.find("$resumePlan.Add"),
        )

    def test_recovery_verifies_temp_and_final_files_without_overwrite(self) -> None:
        executable = mask_comments_and_strings(self.repair_body)
        assert_patterns_in_order(
            self,
            executable,
            (
                r"\[IO\.File\]::Copy\(\$item\.Source,\s*\$temporaryPath,\s*\$false\)",
                r"Get-BackupFileHash\s+-LiteralPath\s+\$temporaryPath",
                r"Test-Path\s+-LiteralPath\s+\$item\.Destination",
                r"\[IO\.File\]::Move\(\$temporaryPath,\s*\$item\.Destination\)",
                r"foreach\s*\(\$entry\s+in\s+\$entries\)",
                r"Get-BackupFileHash\s+-LiteralPath\s+\$destinationPath",
                r"\$verified\s*\+=\s*1",
            ),
            "copy, race check, and final verification contract",
        )

    def test_audit_and_return_keep_incident_status_separate_from_result(self) -> None:
        expected_lines = (
            '"run_id=$($manifest.run_id)"',
            '"incident_status=$($manifest.status)"',
            '"native_exit_code=$($evidence.NativeExitCode)"',
            "'evidence=manifest+transcript'",
            '"preserved_count=$($preserved.Count)"',
            '"resumed_count=$($resumed.Count)"',
            '"verified_count=$verified"',
            '"preserved=$($preserved -join \',\')"',
            '"resumed=$($resumed -join \',\')"',
            "'result=recovered'",
        )
        cursor = self.repair_body.find("$auditLines")
        self.assertGreaterEqual(cursor, 0)
        for line in expected_lines:
            found = self.repair_body.find(line, cursor)
            self.assertGreater(found, cursor, f"missing or out-of-order audit field {line}")
            cursor = found

        self.assertRegex(
            self.repair_body,
            r'\$audit\s*=\s*\(\$auditLines\s+-join\s+"`n"\)\s*\+\s*"`n"',
        )
        self.assertRegex(
            self.repair_body,
            r"\[IO\.File\]::WriteAllText\(\$AuditPath,\s*\$audit,\s*"
            r"\[Text\.UTF8Encoding\]::new\(\$false\)\)",
        )
        executable = mask_comments_and_strings(self.repair_body)
        self.assertRegex(executable, r"(?im)^\s*IncidentStatus\s*=\s*\[string\]\s*\$manifest\.status\s*$")
        self.assertRegex(executable, r"(?im)^\s*Result\s*=\s+.+$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
