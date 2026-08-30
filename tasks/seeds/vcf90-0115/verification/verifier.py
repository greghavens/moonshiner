#!/usr/bin/env python3
import json
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parent
EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
EXPECTED_OPTIONALS = {
    "limit",
    "taskStatus",
    "taskType",
    "resourceId",
    "resourceType",
    "completedAfter",
    "pageNumber",
    "pageSize",
    "orderDirection",
    "orderBy",
    "taskName",
    "doLiveRefresh",
}


def fail(message):
    raise AssertionError(message)


def verify_contract():
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text())
    contract = json.loads((ROOT / "docs" / "contract.json").read_text())
    if sources["tag"] != "9.0.0.0":
        fail("official source is not tag 9.0.0.0")
    if sources["commitSha"] != EXPECTED_SHA:
        fail("official source commit SHA changed")
    if sources["specPath"] != EXPECTED_SPEC_PATH:
        fail("official source spec path changed")
    if sources["operationIds"] != ["getTasks"]:
        fail("official source must name exactly getTasks")
    if contract["info"]["version"] != "9.0.0.0":
        fail("contract is not the VCF Installer 9.0 contract")
    operation = contract["paths"]["/v1/tasks"]["get"]
    if operation["operationId"] != "getTasks":
        fail("contract operationId mismatch")
    parameters = operation["parameters"]
    names = {item["name"] for item in parameters if item["in"] == "query"}
    if names != EXPECTED_OPTIONALS:
        fail("contract query parameter set mismatch")
    if any(item["required"] for item in parameters):
        fail("getTasks query parameters must all be optional")
    response_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    if response_ref != "#/components/schemas/PageOfTask":
        fail("getTasks response schema mismatch")


def read_requests(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def verify_requests(entries):
    expected_pages = [
        (0, 2),
        (1, 2),
        (2, 2),
        (0, 3),
        (1, 3),
    ]
    if len(entries) != len(expected_pages):
        fail(f"expected {len(expected_pages)} requests, got {len(entries)}")

    unset = EXPECTED_OPTIONALS - {
        "pageNumber", "pageSize", "orderBy", "orderDirection"
    }
    for entry, (page_number, page_size) in zip(entries, expected_pages):
        if entry["method"] != "GET":
            fail("getTasks must use GET")
        if entry["bodyLength"] != 0:
            fail("GET request must not contain a body")
        headers = {}
        for name, value in entry["headers"]:
            headers.setdefault(name.lower(), []).append(value)
        if headers.get("accept") != ["application/json"]:
            fail("Accept header must be exactly application/json")
        if "content-type" in headers:
            fail("bodyless GET must omit Content-Type")
        split = urlsplit(entry["target"])
        if split.path != "/v1/tasks":
            fail(f"unexpected request path: {split.path}")
        pairs = parse_qsl(split.query, keep_blank_values=True)
        query = {}
        for name, value in pairs:
            query.setdefault(name, []).append(value)
        expected_query = {
            "pageNumber": [str(page_number)],
            "pageSize": [str(page_size)],
            "orderBy": ["creationTimestamp"],
            "orderDirection": ["ASC"],
        }
        if query != expected_query:
            fail(f"unexpected query for page {page_number}: {query}")
        for name in unset:
            if name in query:
                fail(f"unset optional field {name} was sent")
        if any(value == "" for values in query.values() for value in values):
            fail("query contains an empty value")


def main():
    verify_contract()
    with tempfile.TemporaryDirectory(prefix="vcf-installer-verify-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "-d",
                str(classes),
                str(ROOT / "src" / "VcfInstallerClient.java"),
                str(ROOT / "tests" / "TestMain.java"),
            ],
            text=True,
            capture_output=True,
        )
        if compile_result.returncode != 0:
            fail("javac failed:\n" + compile_result.stdout + compile_result.stderr)

        ready_file = temp / "port"
        request_log = temp / "requests.jsonl"
        server = subprocess.Popen(
            [
                "python3",
                str(ROOT / "mock_server.py"),
                "--contract",
                str(ROOT / "docs" / "contract.json"),
                "--ready-file",
                str(ready_file),
                "--request-log",
                str(request_log),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready_file.exists() and time.monotonic() < deadline:
                if server.poll() is not None:
                    stdout, stderr = server.communicate()
                    fail("mock server exited early:\n" + stdout + stderr)
                time.sleep(0.02)
            if not ready_file.exists():
                fail("mock server did not become ready")

            port = ready_file.read_text().strip()
            run_results = []
            for page_size in (2, 3):
                run_result = subprocess.run(
                    [
                        "java",
                        "-cp",
                        str(classes),
                        "TestMain",
                        f"http://127.0.0.1:{port}",
                        str(page_size),
                    ],
                    text=True,
                    capture_output=True,
                    timeout=15,
                )
                if run_result.returncode != 0:
                    fail(
                        f"TestMain failed for page size {page_size}:\n"
                        + run_result.stdout
                        + run_result.stderr
                    )
                run_results.append(run_result)
        finally:
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=3)

        expected_output = "\n".join(
            [
                "task-a\t2025-04-10T08:00:00Z\tSUCCESSFUL\tPrepare hosts",
                "task-z\t2025-04-10T08:00:00Z\tIN_PROGRESS\tPrepare \"VCF\"",
                "task-m\t2025-04-10T09:30:00Z\tSUCCESSFUL\tValidate network",
                "task-b\t2025-04-10T10:15:00Z\tFAILED\tDeploy management domain",
                "task-y\t2025-04-10T11:45:00Z\tSUCCESSFUL\tFinalize",
                "task-c\t2025-04-10T12:00:00Z\tSUCCESSFUL\tArchive logs",
            ]
        ) + "\n"
        for page_size, run_result in zip((2, 3), run_results):
            if run_result.stdout != expected_output:
                fail(
                    f"unexpected stable output for page size {page_size}:\n"
                    + run_result.stdout
                )
            if run_result.stderr:
                fail(f"unexpected stderr for page size {page_size}:\n{run_result.stderr}")
        verify_requests(read_requests(request_log))

    print("PASS: complete paginated collection and exact getTasks wire contract")


if __name__ == "__main__":
    main()
