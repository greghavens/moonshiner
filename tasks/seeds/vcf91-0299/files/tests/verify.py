#!/usr/bin/env python3
"""Protected verification for the VCF Operations for Networks tier rollout task.

Runs the package's table-driven contract tests under the race detector. The
tests drive a loopback mock (internal/mockni) pinned to docs/contract.json; no
VMware endpoint or any other network host is contacted.
"""

import os
import subprocess
import tempfile
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    # Keep every cache and user-level Go setting out of the host environment.
    # This also gives ccache (used by some Go installations for -race builds) a
    # writable home directory.
    with tempfile.TemporaryDirectory(prefix="vcfops-verify-") as temp_home:
        env = dict(os.environ)
        env["HOME"] = temp_home
        env["GOCACHE"] = os.path.join(temp_home, "go-build")
        env["GOMODCACHE"] = os.path.join(temp_home, "go-mod")
        env["GOENV"] = "off"
        # The module is stdlib-only. A missing dependency must surface as a
        # build error rather than causing a network fetch.
        env["GOPROXY"] = "off"
        env["GOFLAGS"] = "-mod=mod"
        env["GOTOOLCHAIN"] = "local"

        cmd = ["go", "test", "-race", "-count=1", "./..."]
        print("+ " + " ".join(cmd), flush=True)
        try:
            proc = subprocess.run(
                cmd, cwd=ROOT, env=env, timeout=540,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        except subprocess.TimeoutExpired:
            print("FAIL: go test timed out", file=sys.stderr)
            return 1
        except FileNotFoundError:
            print("FAIL: the go toolchain is not available on PATH", file=sys.stderr)
            return 1

    print(proc.stdout, end="", flush=True)

    if proc.returncode != 0:
        print("\nFAIL: contract tests did not pass", file=sys.stderr)
        return 1

    print("\nPASS: contract tests passed under -race")
    return 0


if __name__ == "__main__":
    sys.exit(main())
