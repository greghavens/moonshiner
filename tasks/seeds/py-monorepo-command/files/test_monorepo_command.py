import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from repoctl.command import member_lines
from repoctl.discovery import WorkspaceNotFound, discover_workspace


EXPECTED = (
    "queue-worker\tworker\tpackages/worker\n"
    "gateway-api\tservice\tpackages/api\n"
)


def run(*args: str, cwd: Path, module_path: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if module_path:
        env["PYTHONPATH"] = str(TOOLS)
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def check_root_module_entrypoint():
    result = run("-m", "repoctl", "members", cwd=ROOT, module_path=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == EXPECTED
    assert result.stderr == ""


def check_console_entrypoint_from_member():
    member = ROOT / "packages" / "worker"
    result = run(str(ROOT / "bin" / "repo-members"), "members", cwd=member)
    assert result.returncode == 0, (
        f"console entrypoint from packages/worker exited {result.returncode}: "
        f"{result.stderr.strip()}"
    )
    assert result.stdout == EXPECTED
    assert result.stderr == ""


def check_shared_command_from_deep_member_path():
    lines = member_lines(ROOT / "packages" / "api")
    assert lines == [
        "queue-worker\tworker\tpackages/worker",
        "gateway-api\tservice\tpackages/api",
    ]


def check_nearest_nested_workspace_wins():
    nested_member = ROOT / "examples" / "nested-workspace" / "projects" / "widget"
    assert discover_workspace(nested_member) == ROOT / "examples" / "nested-workspace"
    assert member_lines(nested_member) == [
        "demo-widget\texample\tprojects/widget",
    ]


def check_unrelated_location_still_fails():
    isolated = ROOT.parent
    try:
        discover_workspace(isolated)
        raise AssertionError("an unrelated location must not acquire a workspace")
    except WorkspaceNotFound as exc:
        assert str(exc) == f"no workspace.toml at {isolated.resolve()}"


def check_usage_contract():
    result = run(str(ROOT / "bin" / "repo-members"), cwd=ROOT)
    assert result.returncode == 64
    assert result.stdout == ""
    assert result.stderr == "usage: repo-members members\n"


def main():
    check_root_module_entrypoint()
    check_console_entrypoint_from_member()
    check_shared_command_from_deep_member_path()
    check_nearest_nested_workspace_wins()
    check_unrelated_location_still_fails()
    check_usage_contract()
    print("all monorepo command checks passed")


if __name__ == "__main__":
    main()
