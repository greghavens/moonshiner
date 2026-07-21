from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CHECKER = ROOT / "tools" / "check_abi.py"


class AbiReleaseGateTests(unittest.TestCase):
    maxDiff = None

    def run_gate_paths(
        self, baseline: Path, candidate: Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                str(baseline),
                str(candidate),
            ],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def run_gate(self, candidate: str) -> subprocess.CompletedProcess[str]:
        return self.run_gate_paths(FIXTURES / "baseline", FIXTURES / candidate)

    def run_with_symbol_replacement(
        self, old: str, new: str
    ) -> subprocess.CompletedProcess[str]:
        baseline = FIXTURES / "baseline"
        symbols = (baseline / "symbols.txt").read_text(encoding="utf-8")
        self.assertEqual(symbols.count(old), 1)
        with tempfile.TemporaryDirectory() as temporary_directory:
            candidate = Path(temporary_directory)
            (candidate / "symbols.txt").write_text(
                symbols.replace(old, new), encoding="utf-8"
            )
            shutil.copyfile(baseline / "layouts.txt", candidate / "layouts.txt")
            return self.run_gate_paths(baseline, candidate)

    def run_with_layout_replacement(
        self, old: str, new: str
    ) -> subprocess.CompletedProcess[str]:
        baseline = FIXTURES / "baseline"
        layouts = (baseline / "layouts.txt").read_text(encoding="utf-8")
        self.assertEqual(layouts.count(old), 1)
        with tempfile.TemporaryDirectory() as temporary_directory:
            candidate = Path(temporary_directory)
            shutil.copyfile(baseline / "symbols.txt", candidate / "symbols.txt")
            (candidate / "layouts.txt").write_text(
                layouts.replace(old, new), encoding="utf-8"
            )
            return self.run_gate_paths(baseline, candidate)

    def test_unchanged_contract_preserves_compatibility_aliases(self) -> None:
        result = self.run_gate("unchanged")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "ABI compatible\n")
        self.assertEqual(result.stderr, "")

    def test_removed_compatibility_alias_is_an_abi_break(self) -> None:
        result = self.run_gate("alias_removed")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            "ABI BREAK detected\n"
            "Removed or changed public symbols:\n"
            "  - FUNC orbit_session_open_v1@ORBIT_1.0 "
            "(alias of orbit_session_open)\n",
        )
        self.assertEqual(result.stderr, "")

    def test_changed_symbol_kind_is_an_abi_break(self) -> None:
        result = self.run_with_symbol_replacement(
            "OBJECT|orbit_api_level|ORBIT_2.0|default|-",
            "FUNC|orbit_api_level|ORBIT_2.0|default|-",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("OBJECT orbit_api_level@@ORBIT_2.0", result.stdout)

    def test_changed_default_version_status_is_an_abi_break(self) -> None:
        result = self.run_with_symbol_replacement(
            "FUNC|orbit_session_close|ORBIT_2.0|default|-",
            "FUNC|orbit_session_close|ORBIT_2.0|compat|-",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FUNC orbit_session_close@@ORBIT_2.0", result.stdout)

    def test_changed_alias_target_is_an_abi_break(self) -> None:
        result = self.run_with_symbol_replacement(
            "FUNC|orbit_session_open_v1|ORBIT_1.0|compat|orbit_session_open",
            "FUNC|orbit_session_open_v1|ORBIT_1.0|compat|orbit_session_close",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "FUNC orbit_session_open_v1@ORBIT_1.0 (alias of orbit_session_open)",
            result.stdout,
        )

    def test_symbol_version_change_is_an_abi_break(self) -> None:
        result = self.run_gate("version_changed")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FUNC orbit_session_close@@ORBIT_2.0", result.stdout)

    def test_record_field_offset_change_is_an_abi_break(self) -> None:
        result = self.run_gate("layout_changed")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("orbit::Session: layout changed", result.stdout)

    def test_record_alignment_change_is_an_abi_break(self) -> None:
        result = self.run_with_layout_replacement(
            "RECORD|orbit::Handle|8|8", "RECORD|orbit::Handle|8|4"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("orbit::Handle: layout changed", result.stdout)

    def test_base_offset_change_is_an_abi_break(self) -> None:
        result = self.run_with_layout_replacement(
            "BASE|orbit::Session|orbit::Handle|0",
            "BASE|orbit::Session|orbit::Handle|8",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("orbit::Session: layout changed", result.stdout)

    def test_field_size_change_is_an_abi_break(self) -> None:
        result = self.run_with_layout_replacement(
            "FIELD|orbit::Session|state_|8|4",
            "FIELD|orbit::Session|state_|8|8",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("orbit::Session: layout changed", result.stdout)

    def test_removed_record_is_an_abi_break(self) -> None:
        baseline = FIXTURES / "baseline"
        layouts = (baseline / "layouts.txt").read_text(encoding="utf-8")
        removed_record = "RECORD|orbit::Handle|8|8\nFIELD|orbit::Handle|value_|0|8\n"
        self.assertEqual(layouts.count(removed_record), 1)
        with tempfile.TemporaryDirectory() as temporary_directory:
            candidate = Path(temporary_directory)
            shutil.copyfile(baseline / "symbols.txt", candidate / "symbols.txt")
            (candidate / "layouts.txt").write_text(
                layouts.replace(removed_record, ""), encoding="utf-8"
            )
            result = self.run_gate_paths(baseline, candidate)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("orbit::Handle: record removed", result.stdout)


if __name__ == "__main__":
    unittest.main()
