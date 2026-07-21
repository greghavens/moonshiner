#!/usr/bin/env python3
"""Protected regression tests for the background-case harness."""

from __future__ import annotations

import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "bin" / "run_case.sh"
PR_GET_CHILD_SUBREAPER = 37
PR_SET_CHILD_SUBREAPER = 36


def child_subreaper_enabled() -> bool:
    """Return whether Linux reparents orphaned descendants to this process."""
    import ctypes

    enabled = ctypes.c_int()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_GET_CHILD_SUBREAPER, ctypes.byref(enabled), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return bool(enabled.value)


def set_child_subreaper(enabled: bool) -> None:
    """Control whether orphaned descendants are reparented to this process."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, int(enabled), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def reap_if_adopted(pid: int | None) -> bool:
    """Reap pid if it became our child; report whether it was adopted."""
    if pid is None:
        return False
    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return False
    return waited_pid == pid


def process_has_exited(pidfd: int) -> bool:
    """Return true once Linux reports that the pidfd's process has exited."""
    readable, _, _ = select.select([pidfd], [], [], 0)
    return bool(readable)


def wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o755)


class BackgroundCleanupTests(unittest.TestCase):
    def test_command_exit_status_is_preserved(self) -> None:
        for expected_status in (0, 23, 64, 129, 255):
            with self.subTest(status=expected_status):
                completed = subprocess.run(
                    [
                        HARNESS,
                        sys.executable,
                        "-c",
                        f"raise SystemExit({expected_status})",
                    ],
                    input=b"",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=4,
                )
                self.assertEqual(
                    completed.returncode,
                    expected_status,
                    completed.stderr.decode(),
                )
                self.assertEqual(completed.stdout, b"")
                self.assertEqual(completed.stderr, b"")

    def test_descendant_cannot_steal_later_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            fixture = tmpdir / "leave_reader.sh"
            pid_record = tmpdir / "reader.pid"
            stolen = tmpdir / "stolen.txt"
            terminated = tmpdir / "terminated"
            release = tmpdir / "release"
            write_executable(
                fixture,
                r"""
                #!/bin/bash
                set -u
                pid_record=$1
                stolen=$2
                terminated=$3
                release=$4
                parent_pid=$BASHPID

                (
                    trap ': >"$terminated"; exit 0' HUP INT TERM
                    printf '%s %s\n' "$parent_pid" "$BASHPID" >"$pid_record.tmp"
                    mv -- "$pid_record.tmp" "$pid_record"
                    if IFS= read -r line; then
                        printf '%s\n' "$line" >"$stolen"
                    fi
                ) <&0 &

                while [[ ! -s $pid_record ]]; do
                    :
                done
                while [[ ! -e $release ]]; do
                    :
                done
                exit 0
                """,
            )

            stdout_capture = tempfile.TemporaryFile()
            stderr_capture = tempfile.TemporaryFile()
            proc = subprocess.Popen(
                [HARNESS, fixture, pid_record, stolen, terminated, release],
                stdin=subprocess.PIPE,
                stdout=stdout_capture,
                stderr=stderr_capture,
            )
            parent_pid = None
            parent_pidfd = None
            reader_pid = None
            reader_pidfd = None
            try:
                self.assertTrue(
                    wait_until(pid_record.exists),
                    "reader did not publish its PID",
                )
                parent_text, reader_text = pid_record.read_text().split()
                parent_pid = int(parent_text)
                reader_pid = int(reader_text)
                parent_pidfd = os.pidfd_open(parent_pid)
                reader_pidfd = os.pidfd_open(reader_pid)

                parent_pgid = os.getpgid(parent_pid)
                reader_pgid = os.getpgid(reader_pid)
                self.assertEqual(
                    reader_pgid,
                    parent_pgid,
                    "a case descendant escaped the case process group",
                )
                self.assertNotEqual(
                    parent_pgid,
                    os.getpgrp(),
                    "the case inherited the test runner's process group",
                )

                release.touch()
                self.assertEqual(proc.wait(timeout=4), 0)
                stdout_capture.seek(0)
                stderr_capture.seek(0)
                self.assertEqual(stdout_capture.read(), b"")
                self.assertEqual(stderr_capture.read(), b"")

                # Model input supplied to the next test while an old reader still
                # owns the pipe. A correct cleanup has already closed every reader.
                try:
                    assert proc.stdin is not None
                    proc.stdin.write(b"NEXT-TEST-TOKEN\n")
                    proc.stdin.flush()
                except BrokenPipeError:
                    pass

                time.sleep(0.05)
                self.assertFalse(stolen.exists(), "a leftover process stole later input")
                self.assertTrue(
                    terminated.exists(),
                    "cleanup skipped graceful TERM before its forced fallback",
                )
                assert reader_pidfd is not None
                self.assertTrue(
                    wait_until(lambda: process_has_exited(reader_pidfd), timeout=1.0),
                    f"reader PID {reader_pid} survived harness exit",
                )
            finally:
                release.touch()
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except BrokenPipeError:
                        pass
                if (
                    reader_pid is not None
                    and reader_pidfd is not None
                    and not process_has_exited(reader_pidfd)
                ):
                    try:
                        os.kill(reader_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if (
                    parent_pid is not None
                    and parent_pidfd is not None
                    and not process_has_exited(parent_pidfd)
                ):
                    try:
                        os.kill(parent_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if reader_pidfd is not None:
                    os.close(reader_pidfd)
                if parent_pidfd is not None:
                    os.close(parent_pidfd)
                if proc.poll() is None:
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                stdout_capture.close()
                stderr_capture.close()

    def test_signal_statuses_and_descendants_are_cleaned(self) -> None:
        previous_subreaper = child_subreaper_enabled()
        set_child_subreaper(True)
        self.addCleanup(set_child_subreaper, previous_subreaper)

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            fixture = tmpdir / "stubborn_tree.py"
            fixture.write_text(
                textwrap.dedent(
                    """
                    import os
                    from pathlib import Path
                    import signal
                    import subprocess
                    import sys

                    if sys.argv[1] == "child":
                        for caught in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                            signal.signal(caught, signal.SIG_IGN)
                        record = Path(sys.argv[2])
                        temporary = record.with_suffix(record.suffix + ".tmp")
                        temporary.write_text(
                            f"{os.getppid()} {os.getpid()}", encoding="utf-8"
                        )
                        temporary.replace(record)
                        while True:
                            signal.pause()
                    else:
                        child = subprocess.Popen(
                            [sys.executable, __file__, "child", sys.argv[2]]
                        )
                        raise SystemExit(child.wait())
                    """
                ).lstrip(),
                encoding="utf-8",
            )

            for sent_signal, expected_status in (
                (signal.SIGHUP, 129),
                (signal.SIGINT, 130),
                (signal.SIGTERM, 143),
            ):
                with self.subTest(signal=sent_signal.name):
                    pid_record = tmpdir / f"{sent_signal.name}.pid"
                    stdout_capture = tempfile.TemporaryFile()
                    stderr_capture = tempfile.TemporaryFile()
                    proc = subprocess.Popen(
                        [HARNESS, sys.executable, fixture, "parent", pid_record],
                        stdin=subprocess.PIPE,
                        stdout=stdout_capture,
                        stderr=stderr_capture,
                    )
                    assert proc.stdin is not None
                    proc.stdin.close()
                    parent_pid = None
                    parent_pidfd = None
                    descendant_pid = None
                    descendant_pidfd = None
                    try:
                        self.assertTrue(
                            wait_until(pid_record.exists),
                            "descendant did not become ready",
                        )
                        parent_text, descendant_text = pid_record.read_text().split()
                        parent_pid = int(parent_text)
                        descendant_pid = int(descendant_text)
                        parent_pidfd = os.pidfd_open(parent_pid)
                        descendant_pidfd = os.pidfd_open(descendant_pid)
                        os.kill(proc.pid, sent_signal)
                        proc.wait(timeout=5)
                        self.assertEqual(
                            proc.returncode,
                            expected_status,
                        )
                        stdout_capture.seek(0)
                        stderr_capture.seek(0)
                        self.assertEqual(stdout_capture.read(), b"")
                        self.assertEqual(stderr_capture.read(), b"")
                        assert parent_pidfd is not None
                        assert descendant_pidfd is not None
                        self.assertTrue(
                            wait_until(
                                lambda: process_has_exited(parent_pidfd), timeout=1.0
                            ),
                            f"tracked child PID {parent_pid} survived {sent_signal.name}",
                        )
                        self.assertTrue(
                            wait_until(
                                lambda: process_has_exited(descendant_pidfd), timeout=1.0
                            ),
                            f"descendant PID {descendant_pid} survived {sent_signal.name}",
                        )
                        self.assertFalse(
                            reap_if_adopted(parent_pid),
                            f"tracked child PID {parent_pid} was not reaped",
                        )
                    finally:
                        if (
                            descendant_pid is not None
                            and descendant_pidfd is not None
                            and not process_has_exited(descendant_pidfd)
                        ):
                            try:
                                os.kill(descendant_pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            wait_until(
                                lambda: process_has_exited(descendant_pidfd),
                                timeout=1.0,
                            )
                        if (
                            parent_pid is not None
                            and parent_pidfd is not None
                            and not process_has_exited(parent_pidfd)
                        ):
                            try:
                                os.kill(parent_pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            wait_until(
                                lambda: process_has_exited(parent_pidfd),
                                timeout=1.0,
                            )
                        reap_if_adopted(descendant_pid)
                        reap_if_adopted(parent_pid)
                        if descendant_pidfd is not None:
                            os.close(descendant_pidfd)
                        if parent_pidfd is not None:
                            os.close(parent_pidfd)
                        if proc.poll() is None:
                            try:
                                proc.wait(timeout=1)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait()
                        stdout_capture.close()
                        stderr_capture.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
