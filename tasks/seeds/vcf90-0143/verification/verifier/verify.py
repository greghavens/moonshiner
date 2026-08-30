#!/usr/bin/env python3
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROTECTED_SHA256 = {
    "docs/contract.json": "a7a89654f38783a61e991d6de8c64c74d520ac96f3d7e21012cb340c8dcbcf8b",
    "docs/official_sources.json": "2c10946074664985acec5082eb8e838c7263ad76dc05596840c91e77c3863c47",
    "verifier/TestMain.java": "b4ed4e4921d577a22cf6ffdae8d363120ed7c26cdd121785a504de4cbdc76a23",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


for relative, expected in PROTECTED_SHA256.items():
    actual = sha256(ROOT / relative)
    if actual != expected:
        fail(f"protected fixture changed: {relative} (expected {expected}, got {actual})")

sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
operation_ids = [entry["operationId"] for entry in sources["operations"]]
if operation_ids != ["listVms"]:
    fail(f"official operationIds must be exactly ['listVms'], got {operation_ids!r}")

with tempfile.TemporaryDirectory(prefix="vcf90-0143-") as temporary:
    classes = pathlib.Path(temporary) / "classes"
    classes.mkdir()
    compile_result = subprocess.run(
        [
            "javac",
            "--release",
            "17",
            "--add-modules",
            "jdk.httpserver",
            "-d",
            str(classes),
            str(ROOT / "src" / "VcfOperationsNetworksClient.java"),
            str(ROOT / "verifier" / "TestMain.java"),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if compile_result.returncode != 0:
        print(compile_result.stdout, end="", file=sys.stderr)
        fail("javac compilation failed")

    run_result = subprocess.run(
        ["java", "--add-modules", "jdk.httpserver", "-cp", str(classes), "TestMain"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
    )
    print(run_result.stdout, end="")
    if run_result.returncode != 0:
        fail("TestMain failed")
