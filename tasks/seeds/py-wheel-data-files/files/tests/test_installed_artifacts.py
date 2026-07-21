from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "Hello, Ada!\nWelcome to Letterpress.\n"


class InstalledArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="letterpress-acceptance-")
        cls.scratch = Path(cls._temporary.name)
        cls.project = cls.scratch / "project"
        shutil.copytree(
            PROJECT_ROOT,
            cls.project,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.egg-info", "build", "dist"
            ),
        )

        cls.env = os.environ.copy()
        cls.env.update(
            {
                "PIP_NO_INDEX": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        cls.env.pop("PYTHONPATH", None)

        wheelhouse = cls.scratch / "wheelhouse"
        wheelhouse.mkdir()
        cls._run(
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--no-cache-dir",
            "--wheel-dir",
            str(wheelhouse),
            cwd=cls.project,
        )
        wheels = sorted(wheelhouse.glob("*.whl"))
        if len(wheels) != 1:
            raise AssertionError(f"expected one wheel, found: {wheels}")
        cls.wheel = wheels[0]

        cls.wheel_site = cls.scratch / "wheel-site"
        cls._run(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-cache-dir",
            "--target",
            str(cls.wheel_site),
            str(cls.wheel),
            cwd=cls.scratch,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    @classmethod
    def _run(cls, *command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=cls.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if completed.returncode != 0:
            rendered = " ".join(command)
            raise AssertionError(
                f"command failed with exit {completed.returncode}: {rendered}\n"
                f"{completed.stdout}"
            )
        return completed

    def test_editable_install_still_renders(self) -> None:
        editable_site = self.scratch / "editable-site"
        self._run(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--no-cache-dir",
            "--editable",
            ".",
            "--target",
            str(editable_site),
            cwd=self.project,
        )
        code = (
            "import site, sys; "
            "site.addsitedir(sys.argv[1]); "
            "from letterpress import render_welcome; "
            f"assert render_welcome('Ada') == {EXPECTED!r}"
        )
        self._run(
            sys.executable,
            "-I",
            "-c",
            code,
            str(editable_site),
            cwd=self.scratch,
        )

    def test_wheel_contains_template(self) -> None:
        with zipfile.ZipFile(self.wheel) as archive:
            self.assertIn("letterpress/templates/welcome.txt", archive.namelist())

    def test_wheel_only_install_renders(self) -> None:
        code = (
            "import sys; "
            "sys.path.insert(0, sys.argv[1]); "
            "from letterpress import render_welcome; "
            f"assert render_welcome('Ada') == {EXPECTED!r}"
        )
        self._run(
            sys.executable,
            "-I",
            "-c",
            code,
            str(self.wheel_site),
            cwd=self.scratch,
        )


if __name__ == "__main__":
    unittest.main()
