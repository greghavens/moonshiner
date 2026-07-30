"""Protected verifier for the focused vCenter Supervisor backup task."""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path
import secrets
import sys
import tempfile
from typing import Any, Callable
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mock_vcenter import (  # noqa: E402
    CREATE_OPERATION,
    GET_TASK_OPERATION,
    ContractMock,
)
from vcf_supervisor_backup import (  # noqa: E402
    BackupResult,
    PollTimeoutError,
    ProtocolError,
    SupervisorBackupClient,
    TaskFailedError,
    VcenterError,
)


CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
EXPECTED_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
EXPECTED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_raises(
    exception_type: type[BaseException],
    action: Callable[[], Any],
    message: str,
) -> BaseException:
    try:
        action()
    except exception_type as exc:
        return exc
    except Exception as exc:  # pragma: no cover - diagnostic path
        raise AssertionError(
            f"{message}: expected {exception_type.__name__}, got "
            f"{type(exc).__name__}"
        ) from exc
    raise AssertionError(f"{message}: no exception was raised")


def read_log(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def header_map(record: dict[str, Any]) -> dict[str, list[str]]:
    headers: dict[str, list[str]] = {}
    for name, value in record["headers"]:
        headers.setdefault(name.lower(), []).append(value)
    return headers


def assert_common_headers(record: dict[str, Any], token: str) -> None:
    headers = header_map(record)
    require(headers.get("accept") == ["application/json"], "wrong Accept header")
    require(
        headers.get("vmware-api-session-id") == [token],
        "wrong vCenter session header",
    )
    require("authorization" not in headers, "Authorization must not be sent")


def assert_provenance() -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    operation_ids = [item["operationId"] for item in contract["operations"]]
    source_operation_ids = [item["operationId"] for item in sources["operations"]]
    expected = [CREATE_OPERATION, GET_TASK_OPERATION]
    require(operation_ids == expected, "contract operationIds changed")
    require(source_operation_ids == expected, "source operationIds changed")
    require(contract["source"]["commit_sha"] == EXPECTED_SHA, "wrong contract SHA")
    require(sources["commit_sha"] == EXPECTED_SHA, "wrong sources SHA")
    require(
        contract["source"]["spec_blob_sha"] == EXPECTED_BLOB,
        "wrong contract blob SHA",
    )
    require(sources["spec_blob_sha"] == EXPECTED_BLOB, "wrong sources blob SHA")
    require(contract["source"]["spec_path"] == EXPECTED_SPEC, "wrong spec path")
    require(sources["spec_path"] == EXPECTED_SPEC, "wrong sources spec path")
    require(contract["server_base_path"] == "/api", "wrong API base path")
    return contract


def assert_constructor_validation() -> None:
    token = "validation-" + secrets.token_urlsafe(12)
    invalid_origins = [
        "/relative",
        "ftp://127.0.0.1",
        "http://user:pass@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1/?x=1",
        "http://127.0.0.1/#fragment",
    ]
    for origin in invalid_origins:
        require_raises(
            ValueError,
            lambda value=origin: SupervisorBackupClient(value, token),
            f"invalid origin accepted: {origin}",
        )
    for bad_token in ["", "   ", "bad\nheader", "bad\rheader"]:
        require_raises(
            ValueError,
            lambda value=bad_token: SupervisorBackupClient(
                "http://127.0.0.1:9", value
            ),
            "invalid session token accepted",
        )
    for bad_timeout in [True, 0, -1, math.inf, math.nan]:
        require_raises(
            ValueError,
            lambda value=bad_timeout: SupervisorBackupClient(
                "http://127.0.0.1:9", token, timeout=value
            ),
            "invalid timeout accepted",
        )
    for bad_interval in [True, -0.1, math.inf, math.nan]:
        require_raises(
            ValueError,
            lambda value=bad_interval: SupervisorBackupClient(
                "http://127.0.0.1:9", token, poll_interval=value
            ),
            "invalid poll interval accepted",
        )
    for bad_limit in [True, 0, -1, 1.5]:
        require_raises(
            ValueError,
            lambda value=bad_limit: SupervisorBackupClient(
                "http://127.0.0.1:9", token, max_polls=value
            ),
            "invalid max_polls accepted",
        )
    SupervisorBackupClient("http://127.0.0.1:9/", token)


def run_primary_case(directory: Path) -> None:
    token = "session-" + secrets.token_urlsafe(24)
    supervisor = "supervisor " + secrets.token_urlsafe(9) + "/blue"
    comment = "nightly " + secrets.token_urlsafe(7) + " café"
    task_id = "task/" + secrets.token_urlsafe(18)
    result_value = {
        "archive": "archive-" + secrets.token_urlsafe(12),
        "retained": True,
    }
    log_path = directory / "primary.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        task_id=task_id,
        states=["PENDING", "RUNNING", "BLOCKED", "SUCCEEDED"],
        result=result_value,
    ) as mock:
        client = SupervisorBackupClient(
            mock.base_url + "/",
            token,
            timeout=3.0,
            poll_interval=0.0,
            max_polls=6,
        )
        outcome = client.create_backup(supervisor, comment=comment)

    require(isinstance(outcome, BackupResult), "wrong success result type")
    require(outcome.task_id == task_id, "task identifier was not preserved")
    require(outcome.status == "SUCCEEDED", "wrong terminal status")
    require(outcome.result == result_value, "terminal result was not preserved")
    require(outcome.poll_count == 4, "non-terminal states were not all polled")

    records = read_log(log_path)
    require(len(records) == 5, "primary case must issue one POST and four GETs")
    require(
        [record["sequence"] for record in records] == [1, 2, 3, 4, 5],
        "request log sequence is inconsistent",
    )
    require(
        [record["operation_id"] for record in records]
        == [CREATE_OPERATION] + [GET_TASK_OPERATION] * 4,
        "unexpected operation request sequence",
    )

    submit = records[0]
    expected_submit_target = (
        "/api/vcenter/namespace-management/supervisors/"
        + quote(supervisor, safe="")
        + "/recovery/backup/jobs"
    )
    require(submit["method"] == "POST", "backup create must use POST")
    require(
        submit["raw_target"] == expected_submit_target,
        "Supervisor path segment was not encoded exactly",
    )
    assert_common_headers(submit, token)
    submit_headers = header_map(submit)
    require(
        submit_headers.get("content-type")
        == ["application/json; charset=utf-8"],
        "wrong POST Content-Type",
    )
    expected_body = json.dumps(
        {"comment": comment},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    actual_body = base64.b64decode(submit["body_base64"], validate=True)
    require(actual_body == expected_body, "POST JSON bytes are not exact")
    require(
        submit["body_length"] == len(expected_body),
        "POST body length is inconsistent",
    )
    require(
        submit_headers.get("content-length") == [str(len(expected_body))],
        "POST Content-Length is inconsistent",
    )
    parsed_body = json.loads(actual_body)
    require(list(parsed_body) == ["comment"], "POST body property shape changed")
    require(
        "ignore_health_check_failure" not in parsed_body,
        "unset ignore_health_check_failure must be omitted",
    )

    expected_poll_target = "/api/cis/tasks/" + quote(task_id, safe="")
    for poll in records[1:]:
        require(poll["method"] == "GET", "task polling must use GET")
        require(
            poll["raw_target"] == expected_poll_target,
            "task target or query shape is wrong",
        )
        require("?" not in poll["raw_target"], "task GET must be queryless")
        assert_common_headers(poll, token)
        poll_headers = header_map(poll)
        require("content-type" not in poll_headers, "GET sent Content-Type")
        require("content-length" not in poll_headers, "GET sent Content-Length")
        require(poll["body_length"] == 0, "GET sent a request body")
        require(
            base64.b64decode(poll["body_base64"], validate=True) == b"",
            "GET body log is inconsistent",
        )


def run_explicit_false_case(directory: Path) -> None:
    token = "session-" + secrets.token_urlsafe(20)
    task_id = "task-" + secrets.token_urlsafe(14)
    supervisor = "sup-" + secrets.token_urlsafe(8)
    log_path = directory / "explicit-false.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        task_id=task_id,
        states=["SUCCEEDED"],
        result=None,
    ) as mock:
        result = SupervisorBackupClient(
            mock.base_url,
            token,
            max_polls=1,
        ).create_backup(
            supervisor,
            ignore_health_check_failure=False,
        )

    require(result.poll_count == 1, "terminal first poll was not returned")
    records = read_log(log_path)
    require(len(records) == 2, "explicit-false case request count is wrong")
    raw = base64.b64decode(records[0]["body_base64"], validate=True)
    require(
        raw == b'{"ignore_health_check_failure":false}',
        "explicit false was dropped or unset comment was serialized",
    )


def run_failed_case(directory: Path) -> None:
    token = "session-" + secrets.token_urlsafe(20)
    task_id = "task-" + secrets.token_urlsafe(14)
    hidden_failure = {
        "id": "failure-" + secrets.token_urlsafe(10),
        "secret_detail": secrets.token_urlsafe(24),
    }
    log_path = directory / "failed.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        task_id=task_id,
        states=["FAILED"],
        result=None,
        failed_info=hidden_failure,
    ) as mock:
        client = SupervisorBackupClient(mock.base_url, token, max_polls=3)
        error = require_raises(
            TaskFailedError,
            lambda: client.create_backup("supervisor-failed"),
            "FAILED task was not rejected",
        )

    require(getattr(error, "task_id", None) == task_id, "failed task id missing")
    require(
        getattr(error, "task_info", {}).get("error") == hidden_failure,
        "failed task information missing",
    )
    rendered = repr(error) + str(error)
    require(token not in rendered, "task failure exposed session token")
    require(
        hidden_failure["secret_detail"] not in rendered,
        "task failure exposed task error data",
    )
    require(len(read_log(log_path)) == 2, "FAILED case over-polled")


def run_timeout_case(directory: Path) -> None:
    token = "session-" + secrets.token_urlsafe(20)
    task_id = "task-" + secrets.token_urlsafe(14)
    log_path = directory / "timeout.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        task_id=task_id,
        states=["RUNNING"],
        result=None,
    ) as mock:
        client = SupervisorBackupClient(mock.base_url, token, max_polls=2)
        error = require_raises(
            PollTimeoutError,
            lambda: client.create_backup("supervisor-timeout"),
            "poll exhaustion was not reported",
        )

    require(getattr(error, "task_id", None) == task_id, "timeout task id missing")
    require(getattr(error, "max_polls", None) == 2, "timeout poll limit missing")
    records = read_log(log_path)
    require(len(records) == 3, "timeout must issue one POST and exactly two GETs")
    require(
        [record["operation_id"] for record in records]
        == [CREATE_OPERATION, GET_TASK_OPERATION, GET_TASK_OPERATION],
        "timeout request sequence is wrong",
    )


def assert_public_types() -> None:
    require(issubclass(VcenterError, RuntimeError), "VcenterError is not public")
    require(issubclass(ProtocolError, RuntimeError), "ProtocolError is not public")
    require(
        issubclass(TaskFailedError, RuntimeError),
        "TaskFailedError is not public",
    )
    require(
        issubclass(PollTimeoutError, RuntimeError),
        "PollTimeoutError is not public",
    )


def main() -> None:
    assert_provenance()
    assert_public_types()
    assert_constructor_validation()
    with tempfile.TemporaryDirectory(prefix="vcf91-0099-") as raw_directory:
        directory = Path(raw_directory)
        run_primary_case(directory)
        run_explicit_false_case(directory)
        run_failed_case(directory)
        run_timeout_case(directory)
    print("PASS: vCenter Supervisor backup contract verified")


if __name__ == "__main__":
    main()
