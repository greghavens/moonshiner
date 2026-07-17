"""Run external checker tools and capture their reports.

The audit CLI shells out to one checker per rule pack (linters, license
scanners, secret scanners, ...) and folds each tool's stdout into the
combined report; stderr is kept for the troubleshooting log. Checkers
can be chatty — a full-repo scan happily dumps tens of thousands of
finding lines to stdout — and the runner has to capture all of it.
"""
import subprocess


class ToolResult:
    def __init__(self, argv, returncode, stdout, stderr):
        self.argv = list(argv)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self):
        return self.returncode == 0

    def findings(self):
        """Non-blank stdout lines — one finding per line by convention."""
        return [line for line in self.stdout.splitlines() if line.strip()]

    def __repr__(self):
        return f"ToolResult({self.argv[0]!r}, rc={self.returncode})"


def run_tool(argv, timeout=None):
    """Run one checker to completion and capture its output.

    Blocks until the tool exits (or *timeout* seconds pass, when given),
    then returns a ToolResult with the full stdout/stderr text.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    returncode = proc.wait(timeout=timeout)
    stdout = proc.stdout.read()
    stderr = proc.stderr.read()
    return ToolResult(argv, returncode, stdout, stderr)


def run_all(argvs, timeout=None):
    """Run each checker in sequence; returns the list of results."""
    return [run_tool(argv, timeout=timeout) for argv in argvs]
