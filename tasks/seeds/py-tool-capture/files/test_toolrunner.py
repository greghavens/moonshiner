"""Behavior checks for the tool runner. Run: python3 test_toolrunner.py"""
import sys
import threading

from toolrunner import run_tool


def test_quiet_tool():
    res = run_tool([sys.executable, "-c", "print('ok: no findings')"])
    assert res.ok and res.returncode == 0, res
    assert res.findings() == ["ok: no findings"], res.findings()


def test_failing_tool_keeps_stderr():
    res = run_tool([
        sys.executable, "-c",
        "import sys; sys.stderr.write('rule pack corrupt\\n'); sys.exit(3)",
    ])
    assert not res.ok and res.returncode == 3, res
    assert "rule pack corrupt" in res.stderr, res.stderr
    assert res.findings() == []


def test_chatty_tool_full_repo_scan():
    # A realistic full-repo lint: tens of thousands of finding lines.
    script = "for i in range(40000): print(f'warn: unused import mod_{i}')"
    box = {}

    def worker():
        box["res"] = run_tool([sys.executable, "-c", script])

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), (
        "run_tool never came back from a chatty checker — the audit CLI "
        "would sit there forever")

    res = box["res"]
    assert res.ok, res
    lines = res.findings()
    assert len(lines) == 40000, f"captured {len(lines)} of 40000 findings"
    assert lines[0] == "warn: unused import mod_0"
    assert lines[-1] == "warn: unused import mod_39999"


def main():
    test_quiet_tool()
    test_failing_tool_keeps_stderr()
    test_chatty_tool_full_repo_scan()
    print("all checks passed")


if __name__ == "__main__":
    main()
