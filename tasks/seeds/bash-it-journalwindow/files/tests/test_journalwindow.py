from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "journalwindow"


def record(realtime: int, monotonic: int, message: str, **fields: str) -> dict[str, str]:
    result = {
        "__REALTIME_TIMESTAMP": str(realtime),
        "__MONOTONIC_TIMESTAMP": str(monotonic),
        "_BOOT_ID": "boot-a",
        "_SYSTEMD_UNIT": "api.service",
        "PRIORITY": "4",
        "MESSAGE": message,
    }
    result.update(fields)
    return result


class JournalWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        directory = Path(self.temporary.name)
        self.fixture = directory / "fixture.jsonl"
        self.arguments = directory / "arguments.bin"
        self.fake_journalctl = directory / "journalctl-stub"
        self.fake_journalctl.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            ": \"${JOURNALWINDOW_TEST_ARGS:?}\"\n"
            ": \"${JOURNALWINDOW_TEST_FIXTURE:?}\"\n"
            "printf '%s\\0' \"$@\" >\"$JOURNALWINDOW_TEST_ARGS\"\n"
            "exec /bin/cat \"$JOURNALWINDOW_TEST_FIXTURE\"\n",
            encoding="utf-8",
        )
        self.fake_journalctl.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_program(self, entries: list[dict[str, str]], *arguments: str) -> subprocess.CompletedProcess[str]:
        self.fixture.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in entries),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update(
            JOURNALWINDOW_JOURNALCTL=str(self.fake_journalctl),
            JOURNALWINDOW_TEST_ARGS=str(self.arguments),
            JOURNALWINDOW_TEST_FIXTURE=str(self.fixture),
        )
        return subprocess.run(
            [str(PROGRAM), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def recorded_arguments(self) -> list[str]:
        encoded = self.arguments.read_bytes()
        return [item.decode() for item in encoded.split(b"\0") if item]

    def test_filters_are_forwarded_and_reproduction_is_shell_exact(self) -> None:
        result = self.run_program(
            [],
            "--unit",
            "api worker.service",
            "--unit=sidecar.service",
            "--severity",
            "warning",
            "--boot",
            "-1",
            "--since",
            "2025-01-01 10:00:00",
            "--until=2025-01-01 10:15:00",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        expected_arguments = [
            "--no-pager",
            "--output=json",
            "--unit",
            "api worker.service",
            "--unit",
            "sidecar.service",
            "--priority",
            "warning",
            "--boot",
            "-1",
            "--since",
            "2025-01-01 10:00:00",
            "--until",
            "2025-01-01 10:15:00",
        ]
        self.assertEqual(self.recorded_arguments(), expected_arguments)
        self.assertEqual(
            result.stdout,
            "reproduce: "
            + str(self.fake_journalctl)
            + " --no-pager --output=json --unit api\\ worker.service"
            + " --unit sidecar.service --priority warning --boot -1"
            + " --since 2025-01-01\\ 10:00:00"
            + " --until 2025-01-01\\ 10:15:00\n",
        )

    def test_multiline_messages_and_literal_redaction(self) -> None:
        result = self.run_program(
            [
                record(
                    1_700_000_000_125_000,
                    10,
                    "token=sek.ret\ncontinuation sek.ret",
                    _SYSTEMD_UNIT="worker-sek.ret.service",
                )
            ],
            "--redact",
            "sek.ret",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unit=worker-[REDACTED].service", result.stdout)
        self.assertIn(
            "token=[REDACTED]\ncontinuation [REDACTED]\n--\n", result.stdout
        )
        self.assertNotIn("sek.ret", result.stdout)

    def test_clock_reversal_across_seconds_is_reported(self) -> None:
        result = self.run_program(
            [
                record(1_700_000_002_900_000, 10, "before"),
                record(1_700_000_001_100_000, 20, "after"),
            ]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "warning: clock-order anomaly: boot=boot-a "
            "previous=2023-11-14T22:13:22.900000Z "
            "current=2023-11-14T22:13:21.100000Z\n",
            result.stdout,
        )

    def test_entries_from_different_boots_are_not_compared(self) -> None:
        result = self.run_program(
            [
                record(1_700_000_002_000_000, 10, "old boot", _BOOT_ID="boot-a"),
                record(1_600_000_000_000_000, 20, "new boot", _BOOT_ID="boot-b"),
            ]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("clock-order anomaly", result.stdout)


if __name__ == "__main__":
    unittest.main()
