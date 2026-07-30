"""Protected verifier for the focused asynchronous vCenter clone batch task."""

from __future__ import annotations

import base64
from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path
import secrets
import sys
import tempfile
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mock_vcenter import (  # noqa: E402
    CLONE_OPERATION,
    TASK_LIST_OPERATION,
    ContractMock,
)
from vcf_clone_batch import (  # noqa: E402
    CloneBatchClient,
    CloneRequest,
    CloneResult,
    PollTimeoutError,
    ProtocolError,
    TaskFailedError,
    VcenterError,
)


CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
EXPECTED_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
EXPECTED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
EXPECTED_OPERATIONS = [CLONE_OPERATION, TASK_LIST_OPERATION]


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


def assert_request_headers(record: dict[str, Any], token: str) -> None:
    headers = header_map(record)
    require(headers.get("accept") == ["application/json"], "wrong Accept header")
    require(
        headers.get("vmware-api-session-id") == [token],
        "wrong vCenter session header",
    )
    require("authorization" not in headers, "Authorization must not be sent")
    require(
        headers.get("content-type")
        == ["application/json; charset=utf-8"],
        "wrong JSON Content-Type",
    )
    require(
        headers.get("content-length") == [str(record["body_length"])],
        "wrong Content-Length",
    )


def assert_provenance() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    contract_ids = [item["operationId"] for item in contract["operations"]]
    source_ids = [item["operationId"] for item in sources["operations"]]
    require(contract_ids == EXPECTED_OPERATIONS, "contract operationIds changed")
    require(
        sources["operation_ids"] == EXPECTED_OPERATIONS,
        "official source operationIds changed",
    )
    require(source_ids == EXPECTED_OPERATIONS, "source operation records changed")
    require(contract["source"]["commit_sha"] == EXPECTED_SHA, "wrong contract SHA")
    require(
        sources["repository_commit_sha"] == EXPECTED_SHA,
        "wrong official source SHA",
    )
    require(
        contract["source"]["spec_blob_sha"] == EXPECTED_BLOB,
        "wrong contract blob SHA",
    )
    require(
        sources["spec_blob_sha"] == EXPECTED_BLOB,
        "wrong official source blob SHA",
    )
    require(
        contract["source"]["spec_path"] == EXPECTED_SPEC,
        "wrong contract spec path",
    )
    require(
        sources["spec_path"] == EXPECTED_SPEC,
        "wrong official source spec path",
    )
    require(contract["source"]["license"] == "Apache-2.0", "wrong license")
    require(contract["source"]["openapi"] == "3.0.3", "wrong OpenAPI version")
    require(contract["source"]["api_version"] == "9.1.0.0", "wrong API version")
    require(contract["server_base_path"] == "/api", "wrong API base path")
    for operation in sources["operations"]:
        require(
            operation["repository_commit_sha"] == EXPECTED_SHA,
            "operation source SHA missing",
        )
        require(
            operation["spec_path"] == EXPECTED_SPEC,
            "operation source path missing",
        )
    require(
        contract["operations"][0]["spec_path_item"]
        == "/vcenter/vm?action=clone&vmw-task=true",
        "clone path item changed",
    )
    require(
        contract["operations"][1]["spec_path_item"]
        == "/cis/tasks?action=list",
        "task-list path item changed",
    )


def assert_public_surface() -> None:
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
    sample_request = CloneRequest("vm-1", "clone-1")
    sample_result = CloneResult("task-1", "vm-1", "clone-1", "SUCCEEDED", None, 1)
    require_raises(
        FrozenInstanceError,
        lambda: setattr(sample_request, "name", "changed"),
        "CloneRequest must remain frozen",
    )
    require_raises(
        FrozenInstanceError,
        lambda: setattr(sample_result, "status", "FAILED"),
        "CloneResult must remain frozen",
    )


def assert_constructor_validation() -> None:
    token = "validation-" + secrets.token_urlsafe(12)
    invalid_origins: list[Any] = [
        None,
        "",
        "/relative",
        "ftp://127.0.0.1",
        "http://user:pass@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1/?x=1",
        "http://127.0.0.1/#fragment",
        "http://127.0.0.1:99999",
        "http://127.0.0.1/\n",
    ]
    for origin in invalid_origins:
        require_raises(
            ValueError,
            lambda value=origin: CloneBatchClient(value, token),
            f"invalid origin accepted: {origin!r}",
        )
    for bad_token in [None, "", "   ", "bad\nheader", "bad\rheader"]:
        require_raises(
            ValueError,
            lambda value=bad_token: CloneBatchClient(
                "http://127.0.0.1:9", value
            ),
            "invalid session token accepted",
        )
    for bad_timeout in [True, 0, -1, math.inf, math.nan]:
        require_raises(
            ValueError,
            lambda value=bad_timeout: CloneBatchClient(
                "http://127.0.0.1:9", token, timeout=value
            ),
            "invalid timeout accepted",
        )
    for bad_interval in [True, -0.1, math.inf, math.nan]:
        require_raises(
            ValueError,
            lambda value=bad_interval: CloneBatchClient(
                "http://127.0.0.1:9", token, poll_interval=value
            ),
            "invalid poll interval accepted",
        )
    for bad_limit in [True, 0, -1, 1.5]:
        require_raises(
            ValueError,
            lambda value=bad_limit: CloneBatchClient(
                "http://127.0.0.1:9", token, max_polls=value
            ),
            "invalid max_polls accepted",
        )
    CloneBatchClient("http://127.0.0.1:9/", token)


def assert_batch_validation_before_io() -> None:
    client = CloneBatchClient(
        "http://127.0.0.1:9",
        "validation-" + secrets.token_urlsafe(12),
    )
    invalid_batches: list[Any] = [
        [],
        "not-a-sequence",
        b"not-a-sequence",
        {"source_vm": "vm-1", "name": "clone-1"},
        (item for item in [CloneRequest("vm-1", "clone-1")]),
        [object()],
        [{"source_vm": "vm-1", "name": "clone-1"}],
        [CloneRequest("", "clone-1")],
        [CloneRequest("   ", "clone-1")],
        [CloneRequest("vm-1", "")],
        [CloneRequest("vm-1", " \t ")],
    ]
    for batch in invalid_batches:
        require_raises(
            ValueError,
            lambda value=batch: client.clone_batch(value),
            "invalid batch was not rejected before I/O",
        )


def run_primary_case(directory: Path) -> None:
    token = "session-" + secrets.token_urlsafe(24)
    task_ids = [
        "task-z-" + secrets.token_urlsafe(12),
        "task-a-" + secrets.token_urlsafe(12),
        "task-m-" + secrets.token_urlsafe(12),
    ]
    requests = [
        CloneRequest("vm/" + secrets.token_urlsafe(8), "Zulu café"),
        CloneRequest("vm " + secrets.token_urlsafe(8), "Alpha / blue"),
        CloneRequest("vm-" + secrets.token_urlsafe(8), "Middle Ω"),
    ]
    results = {
        task_ids[0]: {"vm": "clone-z-" + secrets.token_urlsafe(8)},
        task_ids[1]: {"vm": "clone-a-" + secrets.token_urlsafe(8)},
        task_ids[2]: {"vm": "clone-m-" + secrets.token_urlsafe(8)},
    }
    rounds = [
        {
            task_ids[0]: "PENDING",
            task_ids[1]: "RUNNING",
            task_ids[2]: "BLOCKED",
        },
        {
            task_ids[0]: "RUNNING",
            task_ids[1]: "BLOCKED",
            task_ids[2]: "PENDING",
        },
        {
            task_ids[0]: "SUCCEEDED",
            task_ids[1]: "RUNNING",
            task_ids[2]: "SUCCEEDED",
        },
        {
            task_ids[0]: "SUCCEEDED",
            task_ids[1]: "SUCCEEDED",
            task_ids[2]: "SUCCEEDED",
        },
    ]
    log_path = directory / "primary.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        task_ids=task_ids,
        rounds=rounds,
        results=results,
    ) as mock:
        client = CloneBatchClient(
            mock.base_url + "/",
            token,
            timeout=3.0,
            poll_interval=0.0,
            max_polls=6,
        )
        outcome = client.clone_batch(tuple(requests))

    require(isinstance(outcome, list), "clone_batch must return a list")
    require(len(outcome) == 3, "clone_batch returned the wrong item count")
    require(
        all(isinstance(item, CloneResult) for item in outcome),
        "clone_batch returned the wrong item type",
    )
    expected_ids = sorted(task_ids)
    actual_ids = [item.task_id for item in outcome]
    require(
        actual_ids == expected_ids,
        "collection output is not explicitly sorted by task_id",
    )
    by_task = {
        task_id: (request.source_vm, request.name)
        for task_id, request in zip(task_ids, requests)
    }
    for item in outcome:
        source_vm, name = by_task[item.task_id]
        require(item.source_vm == source_vm, "source VM association was lost")
        require(item.name == name, "clone name association was lost")
        require(item.status == "SUCCEEDED", "terminal status was not preserved")
        require(item.result == results[item.task_id], "task result association lost")
        require(item.poll_count == 4, "terminal poll count is wrong")

    records = read_log(log_path)
    require(len(records) == 7, "primary case request count is wrong")
    require(
        [record["sequence"] for record in records] == list(range(1, 8)),
        "request log sequence is inconsistent",
    )
    require(
        [record["operation_id"] for record in records]
        == [CLONE_OPERATION] * 3 + [TASK_LIST_OPERATION] * 4,
        "unexpected operation request sequence",
    )

    for index, record in enumerate(records[:3]):
        request = requests[index]
        require(record["method"] == "POST", "clone must use POST")
        require(
            record["raw_target"]
            == "/api/vcenter/vm?action=clone&vmw-task=true",
            "clone raw target changed",
        )
        assert_request_headers(record, token)
        expected_body = json.dumps(
            {"source": request.source_vm, "name": request.name},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        raw = base64.b64decode(record["body_base64"], validate=True)
        require(raw == expected_body, "clone request JSON bytes are not exact")
        require(record["body_length"] == len(expected_body), "clone length wrong")
        parsed = json.loads(raw)
        require(
            list(parsed) == ["source", "name"],
            "clone body property order or shape changed",
        )
        for optional in (
            "placement",
            "disks_to_remove",
            "disks_to_update",
            "power_on",
            "guest_customization_spec",
        ):
            require(optional not in parsed, f"unset clone field {optional} was sent")

    expected_poll_body = json.dumps(
        {"filter_spec": {"tasks": task_ids}},
        separators=(",", ":"),
    ).encode("utf-8")
    expected_response_orders = [
        task_ids,
        list(reversed(task_ids)),
        task_ids,
        list(reversed(task_ids)),
    ]
    for index, record in enumerate(records[3:]):
        require(record["method"] == "POST", "task list must use POST")
        require(
            record["raw_target"] == "/api/cis/tasks?action=list",
            "task-list raw target changed",
        )
        assert_request_headers(record, token)
        raw = base64.b64decode(record["body_base64"], validate=True)
        require(raw == expected_poll_body, "task-list JSON bytes are not exact")
        require(
            record["response_element_order"] == expected_response_orders[index],
            "mock did not flip collection order on every response",
        )
        parsed = json.loads(raw)
        require(list(parsed) == ["filter_spec"], "result_spec must be omitted")
        require(
            list(parsed["filter_spec"]) == ["tasks"],
            "unset task filters must be omitted",
        )
    terminal_order = records[-1]["response_element_order"]
    require(
        terminal_order != sorted(terminal_order),
        "terminal fixture order must not accidentally be sorted",
    )
    require(
        actual_ids != terminal_order,
        "client preserved terminal server iteration order",
    )


def run_failed_case(directory: Path) -> None:
    token = "session-" + secrets.token_urlsafe(20)
    task_ids = [
        "task-z-" + secrets.token_urlsafe(10),
        "task-a-" + secrets.token_urlsafe(10),
    ]
    hidden_error = {
        "id": "failure-" + secrets.token_urlsafe(10),
        "secret_detail": secrets.token_urlsafe(24),
    }
    rounds = [{task_ids[0]: "RUNNING", task_ids[1]: "FAILED"}]
    log_path = directory / "failed.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        task_ids=task_ids,
        rounds=rounds,
        results={},
        task_errors={task_ids[1]: hidden_error},
    ) as mock:
        client = CloneBatchClient(mock.base_url, token, max_polls=4)
        error = require_raises(
            TaskFailedError,
            lambda: client.clone_batch(
                [
                    CloneRequest("vm-z", "clone-z"),
                    CloneRequest("vm-a", "clone-a"),
                ]
            ),
            "FAILED task was not rejected",
        )

    require(getattr(error, "task_id", None) == task_ids[1], "failed task id missing")
    require(
        getattr(error, "task_info", {}).get("error") == hidden_error,
        "failed task information missing",
    )
    rendered = repr(error) + str(error)
    require(token not in rendered, "task failure exposed the session token")
    require(
        hidden_error["secret_detail"] not in rendered,
        "task failure exposed task error data",
    )
    records = read_log(log_path)
    require(len(records) == 3, "FAILED case must stop after one task-list read")


def run_duplicate_task_case(directory: Path) -> None:
    token = "session-" + secrets.token_urlsafe(20)
    duplicate = "task-duplicate-" + secrets.token_urlsafe(10)
    log_path = directory / "duplicate.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        task_ids=[duplicate, duplicate],
        rounds=[{duplicate: "SUCCEEDED"}],
        results={duplicate: {"vm": "unused"}},
    ) as mock:
        client = CloneBatchClient(mock.base_url, token)
        error = require_raises(
            ProtocolError,
            lambda: client.clone_batch(
                [
                    CloneRequest("vm-1", "clone-1"),
                    CloneRequest("vm-2", "clone-2"),
                ]
            ),
            "duplicate task identifiers were not rejected",
        )

    require(
        getattr(error, "operation_id", None) == CLONE_OPERATION,
        "duplicate-task protocol error has wrong operationId",
    )
    records = read_log(log_path)
    require(len(records) == 2, "duplicate case must submit both clones once")
    require(
        all(record["operation_id"] == CLONE_OPERATION for record in records),
        "duplicate case polled or contacted another route",
    )


def run_timeout_case(directory: Path) -> None:
    token = "session-" + secrets.token_urlsafe(20)
    task_id = "task-" + secrets.token_urlsafe(12)
    log_path = directory / "timeout.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        task_ids=[task_id],
        rounds=[{task_id: "BLOCKED"}],
        results={},
    ) as mock:
        client = CloneBatchClient(mock.base_url, token, max_polls=2)
        error = require_raises(
            PollTimeoutError,
            lambda: client.clone_batch([CloneRequest("vm-1", "clone-1")]),
            "poll exhaustion was not reported",
        )

    require(
        getattr(error, "task_ids", None) == (task_id,),
        "timeout task identifiers missing or reordered",
    )
    require(getattr(error, "max_polls", None) == 2, "timeout poll limit missing")
    records = read_log(log_path)
    require(len(records) == 3, "timeout must submit once and poll exactly twice")
    require(
        [record["operation_id"] for record in records]
        == [CLONE_OPERATION, TASK_LIST_OPERATION, TASK_LIST_OPERATION],
        "timeout request sequence is wrong",
    )


def run_http_error_case(directory: Path) -> None:
    token = "session-" + secrets.token_urlsafe(20)
    hidden = {
        "type": "service_unavailable",
        "secret_detail": secrets.token_urlsafe(24),
    }
    log_path = directory / "http-error.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        task_ids=["unused-task"],
        rounds=[],
        results={},
        clone_error=hidden,
    ) as mock:
        client = CloneBatchClient(mock.base_url, token)
        error = require_raises(
            VcenterError,
            lambda: client.clone_batch([CloneRequest("vm-1", "clone-1")]),
            "clone HTTP failure was not reported",
        )

    require(
        getattr(error, "operation_id", None) == CLONE_OPERATION,
        "HTTP error has wrong operationId",
    )
    require(getattr(error, "status_code", None) == 503, "HTTP status missing")
    require(getattr(error, "payload", None) == hidden, "decoded error payload missing")
    rendered = repr(error) + str(error)
    require(token not in rendered, "HTTP error exposed the session token")
    require(
        hidden["secret_detail"] not in rendered,
        "HTTP error exposed the decoded payload",
    )
    require(len(read_log(log_path)) == 1, "failed clone submission was retried")


def main() -> None:
    assert_provenance()
    assert_public_surface()
    assert_constructor_validation()
    assert_batch_validation_before_io()
    with tempfile.TemporaryDirectory(prefix="vcf91-0107-") as raw_directory:
        directory = Path(raw_directory)
        run_primary_case(directory)
        run_failed_case(directory)
        run_duplicate_task_case(directory)
        run_timeout_case(directory)
        run_http_error_case(directory)
    print("PASS: asynchronous vCenter clone batch contract verified")


if __name__ == "__main__":
    main()
