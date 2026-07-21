import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOWNSTREAM = ROOT / "tests" / "downstream.c"


class InstalledPkgConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool_env = {
            "LC_ALL": "C",
            "PATH": os.environ.get("PATH", os.defpath),
        }
        cls.scratch = tempfile.TemporaryDirectory(
            prefix=".pkgconfig-tests-", dir=ROOT
        )
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.work = Path(cls.scratch.name)
        build_dir = cls.work / "build"
        image = cls.work / "image"
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                f"BUILD_DIR={build_dir}",
                f"DESTDIR={image}",
                "PREFIX=/opt/parcel-sdk",
                "install",
            ],
            cwd=ROOT,
            env=cls.tool_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        if result.returncode != 0:
            raise AssertionError(
                "package build/install failed:\n" + result.stdout + result.stderr
            )

        configured_prefix = image / "opt" / "parcel-sdk"
        cls.prefix = cls.work / "relocated-sdk"
        configured_prefix.rename(cls.prefix)

        pc_dir = cls.prefix / "lib" / "pkgconfig"
        cls.pc_env = {
            **cls.tool_env,
            "PKG_CONFIG_PATH": str(pc_dir),
            "PKG_CONFIG_LIBDIR": str(pc_dir),
        }

    @classmethod
    def pkg_config(cls, *arguments):
        result = subprocess.run(
            ["pkg-config", *arguments, "parcel"],
            env=cls.pc_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        if result.returncode != 0:
            raise AssertionError(
                "pkg-config failed:\n" + result.stdout + result.stderr
            )
        return result.stdout.strip()

    def compile_downstream(self, output, *, static):
        cflags = shlex.split(self.pkg_config("--cflags"))
        library_args = shlex.split(
            self.pkg_config("--static", "--libs")
            if static
            else self.pkg_config("--libs")
        )
        command = [
            "cc",
            "-std=c17",
            "-Wall",
            "-Wextra",
            "-Wpedantic",
            "-Werror",
            str(DOWNSTREAM),
            "-o",
            str(output),
            *cflags,
        ]
        if static:
            command.extend(["-Wl,-Bstatic", *library_args, "-Wl,-Bdynamic"])
        else:
            command.extend(library_args)
        command.append("-Wl,--no-undefined")
        result = subprocess.run(
            command,
            env=self.tool_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        self.assertEqual(
            result.returncode,
            0,
            "downstream compile/link failed:\n" + result.stdout + result.stderr,
        )

    def run_downstream(self, executable, *, shared):
        environment = self.tool_env.copy()
        if shared:
            environment["LD_LIBRARY_PATH"] = str(self.prefix / "lib")
        result = subprocess.run(
            [str(executable)],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "339\n")

    def test_01_prefix_and_include_path_follow_moved_pc_file(self):
        reported_prefix = Path(self.pkg_config("--variable=prefix")).resolve()
        self.assertEqual(reported_prefix, self.prefix.resolve())

        include_flags = shlex.split(self.pkg_config("--cflags"))
        self.assertEqual(len(include_flags), 1)
        self.assertTrue(include_flags[0].startswith("-I"))
        reported_include = Path(include_flags[0][2:]).resolve()
        self.assertEqual(reported_include, (self.prefix / "include").resolve())

    def test_02_shared_metadata_keeps_helpers_private(self):
        link_args = shlex.split(self.pkg_config("--libs"))
        self.assertEqual(len(link_args), 2)
        self.assertTrue(link_args[0].startswith("-L"))
        library_dir = Path(link_args[0][2:]).resolve()
        self.assertEqual(library_dir, (self.prefix / "lib").resolve())
        self.assertEqual(link_args[1:], ["-lparcel"])

    def test_03_shared_downstream_compiles_and_runs(self):
        executable = self.work / "shared-consumer"
        self.compile_downstream(executable, static=False)
        self.run_downstream(executable, shared=True)

    def test_04_static_metadata_preserves_dependency_order(self):
        link_args = shlex.split(self.pkg_config("--static", "--libs"))
        self.assertEqual(len(link_args), 4)
        self.assertTrue(link_args[0].startswith("-L"))
        library_dir = Path(link_args[0][2:]).resolve()
        self.assertEqual(library_dir, (self.prefix / "lib").resolve())
        self.assertEqual(link_args[1:], ["-lparcel", "-lseal", "-lhash"])

    def test_05_static_downstream_compiles_and_runs(self):
        executable = self.work / "static-consumer"
        self.compile_downstream(executable, static=True)
        self.run_downstream(executable, shared=False)


if __name__ == "__main__":
    unittest.main()
