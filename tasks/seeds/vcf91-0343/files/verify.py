#!/usr/bin/env python3
"""Start the loopback fixture and run the protected Java source harness."""

from pathlib import Path
import json
import subprocess
import sys
from urllib.parse import unquote


def check_requests(log_path: Path) -> None:
    requests = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    changes = [
        {
            "id": "project%2Fpayments%2042",
            "authorization": "Bearer fixture-token",
            "project": {
                "name": "Payments Platform",
                "description": "Quarterly \"capacity\" refresh\nrun 0",
            },
            "tags": [
                {"key": "environment", "value": "production"},
                {"key": "owner", "value": "platform-\"ops\""},
            ],
            "zones": [
                {"zoneId": "zone-retired", "priority": 1, "maxNumberInstances": 12}
            ],
        },
        {
            "id": "project%2Fpayments%2042",
            "authorization": "Bearer fixture-token",
            "project": {
                "name": "Payments Platform",
                "description": "Quarterly \"capacity\" refresh\nrun 1",
            },
            "tags": [
                {"key": "environment", "value": "production"},
                {"key": "owner", "value": "platform-\"ops\""},
            ],
            "zones": [
                {"zoneId": "zone-retired", "priority": 1, "maxNumberInstances": 12}
            ],
        },
        {
            "id": "team%3Fred%231",
            "authorization": "Bearer alternate-token",
            "project": {"name": "Delivery 🚀", "description": "Path C:\\deploy\tphase"},
            "tags": [
                {"key": "release\nchannel", "value": "βeta"},
                {"key": "optional", "value": "present"},
            ],
            "zones": [
                {"zoneId": "zone-active", "priority": 2, "maxNumberInstances": 25}
            ],
        },
        {
            "id": "project%20early",
            "authorization": "Bearer fixture-token",
            "project": {
                "name": "rejected",
                "description": "later steps must still execute",
            },
            "tags": [{"key": "restricted", "value": "value"}],
            "zones": [
                {"zoneId": "zone-active", "priority": 3, "maxNumberInstances": 30}
            ],
        },
    ]

    expected_count = len(changes) * 4 + 1
    if len(requests) != expected_count:
        raise AssertionError(
            f"expected exactly {expected_count} requests, received {len(requests)}"
        )

    for run, change in enumerate(changes):
        offset = run * 4
        project_path = "/iaas/api/projects/" + change["id"]
        expected = [
            ("GET", "/iaas/api/projects", "getProjects", None),
            ("PATCH", project_path, "updateProject", change["project"]),
            (
                "PATCH",
                project_path + "/resource-metadata",
                "updateProjectResourceMetadata",
                {"tags": change["tags"]},
            ),
            (
                "PUT",
                project_path + "/zones",
                "updateProjectZoneAssignments",
                {"zoneAssignmentSpecifications": change["zones"]},
            ),
        ]
        for step, (method, path, operation, body) in enumerate(expected):
            request = requests[offset + step]
            if request["method"] != method or unquote(request["path"]) != unquote(path):
                raise AssertionError(f"wrong method or path at run {run}, step {step}")
            if request["operation_id"] != operation:
                raise AssertionError(f"wrong operation at run {run}, step {step}")
            if request["authorization"] != change["authorization"]:
                raise AssertionError(f"wrong bearer token at run {run}, step {step}")
            if body is None:
                if request["body"] != "":
                    raise AssertionError(f"GET sent a body at run {run}")
            else:
                if request["content_type"] != "application/json":
                    raise AssertionError(f"wrong media type at run {run}, step {step}")
                try:
                    actual_body = json.loads(request["body"])
                except json.JSONDecodeError as error:
                    raise AssertionError(
                        f"invalid JSON at run {run}, step {step}: {error}"
                    ) from error
                if actual_body != body:
                    raise AssertionError(
                        f"wrong JSON body at run {run}, step {step}: {actual_body!r}"
                    )

    unsupported = requests[-1]
    if unsupported != {
        "method": "GET",
        "path": "/iaas/api/about",
        "operation_id": None,
        "authorization": None,
        "content_type": None,
        "body": "",
    }:
        raise AssertionError("the loopback service accepted or mislogged an unsupported route")


def main() -> int:
    root = Path(__file__).resolve().parent
    log_path = root / ".mock-request-log.ndjson"
    log_path.unlink(missing_ok=True)
    mock = subprocess.Popen(
        [
            sys.executable,
            str(root / "mock_server.py"),
            "--contract",
            str(root / "docs" / "contract.json"),
            "--log",
            str(log_path),
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert mock.stdout is not None
        port_line = mock.stdout.readline().strip()
        if not port_line.isdigit():
            details = mock.stderr.read() if mock.stderr is not None else ""
            raise RuntimeError(f"mock did not publish a port: {details}")
        result = subprocess.run(
            [
                "java",
                "TestMain.java",
                f"http://127.0.0.1:{port_line}",
                str(log_path),
            ],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=60,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            return result.returncode
        check_requests(log_path)
        print("OK 17 HTTP requests")
        return 0
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mock.kill()
            mock.wait(timeout=5)
        log_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
