#!/usr/bin/env python3
import json
import pathlib
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC = "specifications/vcf-operations/vcf-operations-openapi.json"


def verify_documents():
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))

    assert contract["source"] == {
        "repository": "https://github.com/vmware/vcf-api-specs",
        "tag": "9.0.0.0",
        "commitSha": EXPECTED_SHA,
        "specPath": EXPECTED_SPEC,
    }
    assert contract["serverBasePath"] == "/suite-api"
    assert sources["license"] == "Apache-2.0"
    assert sources["tag"] == "9.0.0.0"
    assert sources["commitSha"] == EXPECTED_SHA
    assert sources["specPath"] == EXPECTED_SPEC
    assert sources["specUrl"] == (
        "https://github.com/vmware/vcf-api-specs/blob/"
        + EXPECTED_SHA
        + "/"
        + EXPECTED_SPEC
    )
    assert sources["operationIds"] == ["performAction", "getActionStatus"]
    assert sources["operations"] == [
        {
            "operationId": "performAction",
            "method": "POST",
            "path": "/api/actions/{id}",
            "sourceLines": "12107-12163",
        },
        {
            "operationId": "getActionStatus",
            "method": "GET",
            "path": "/api/actions/{taskId}/status",
            "sourceLines": "18436-18479",
        },
    ]

    operations = {item["operationId"]: item for item in contract["operations"]}
    assert set(operations) == {"performAction", "getActionStatus"}

    perform = operations["performAction"]
    assert (perform["method"], perform["path"]) == ("POST", "/api/actions/{id}")
    schema = perform["request"]["schema"]
    assert schema["required"] == ["parameterGroup"]
    assert set(schema["properties"]) == {"contextId", "contextResourceId", "parameterGroup"}
    group = schema["properties"]["parameterGroup"]["items"]
    assert group["required"] == ["resourceId"]
    assert set(group["properties"]) == {"resourceId", "parameterValue"}

    status = operations["getActionStatus"]
    assert (status["method"], status["path"]) == (
        "GET",
        "/api/actions/{taskId}/status",
    )
    assert status["queryParameters"] == {
        "detail": {"required": False, "type": "boolean", "default": False}
    }


def compile_and_run():
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        raise SystemExit("Java 17+ is required")

    with tempfile.TemporaryDirectory(prefix="vcf90-0076-") as output:
        compile_result = subprocess.run(
            [
                javac,
                "--release",
                "17",
                "--add-modules",
                "jdk.httpserver",
                "-d",
                output,
                "src/VcfOperationsClient.java",
                "tests/MockVcfOperationsServer.java",
                "tests/TestMain.java",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if compile_result.returncode:
            raise SystemExit(compile_result.stdout)

        run_result = subprocess.run(
            [java, "--add-modules", "jdk.httpserver", "-cp", output, "TestMain"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
        if run_result.returncode:
            raise SystemExit(run_result.stdout)
        print(run_result.stdout, end="")


if __name__ == "__main__":
    verify_documents()
    compile_and_run()
