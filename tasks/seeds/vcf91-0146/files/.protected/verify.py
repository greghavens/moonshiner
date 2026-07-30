"""Protected verifier for the namespace-aware Supervisor backup workflow."""

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

from mock_api import (  # noqa: E402
    CREATE_BACKUP_OPERATION,
    ContractMock,
    GET_NAMESPACE_OPERATION,
    GET_TASK_OPERATION,
    LIST_CLUSTERS_OPERATION,
)
from vcf_namespace_backup import (  # noqa: E402
    ApiError,
    NamespaceBackupClient,
    PollTimeoutError,
    ProtocolError,
    TaskFailedError,
)


CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
EXPECTED_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
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
    except Exception as exc:
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


def assert_provenance() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    require(
        contract["source"]["repositoryCommitSha"] == EXPECTED_SHA,
        "contract repository SHA changed",
    )
    require(
        sources["repositoryCommitSha"] == EXPECTED_SHA,
        "official source repository SHA changed",
    )
    require(
        contract["source"]["specBlobSha"] == EXPECTED_BLOB,
        "contract spec blob SHA changed",
    )
    require(
        sources["specBlobSha"] == EXPECTED_BLOB,
        "official source spec blob SHA changed",
    )
    require(
        contract["source"]["specPath"] == EXPECTED_SPEC,
        "contract spec path changed",
    )
    require(
        sources["specPath"] == EXPECTED_SPEC,
        "official source spec path changed",
    )
    require(contract["source"]["license"] == "Apache-2.0", "wrong license")
    require(contract["source"]["apiVersion"] == "9.1.0.0", "wrong API version")

    expected_vcenter = [
        GET_NAMESPACE_OPERATION,
        CREATE_BACKUP_OPERATION,
        GET_TASK_OPERATION,
    ]
    require(
        sources["operationIds"] == expected_vcenter,
        "official source operationId list changed",
    )
    require(
        [item["operationId"] for item in sources["operations"]]
        == expected_vcenter,
        "official source operation records changed",
    )
    for item in sources["operations"]:
        require(
            item["repositoryCommitSha"] == EXPECTED_SHA,
            "an operation record lacks the pinned repository SHA",
        )
        require(
            item["specPath"] == EXPECTED_SPEC,
            "an operation record lacks the pinned spec path",
        )

    operations = contract["operations"]
    require(
        [item["contractName"] for item in operations]
        == [
            "getSupervisorNamespace",
            "listVksClusters",
            "createSupervisorBackup",
            "getTask",
        ],
        "focused contract operation order changed",
    )
    require(
        operations[0]["operationId"] == GET_NAMESPACE_OPERATION,
        "namespace operationId changed",
    )
    require(
        operations[1]["operationKey"] == LIST_CLUSTERS_OPERATION,
        "Kubernetes operation key changed",
    )
    require(
        "operationId" not in operations[1],
        "Kubernetes route was given a fictional VMware operationId",
    )
    require(
        operations[2]["operationId"] == CREATE_BACKUP_OPERATION,
        "backup operationId changed",
    )
    require(
        operations[3]["operationId"] == GET_TASK_OPERATION,
        "task operationId changed",
    )
    require(
        contract["workflow"]["pollUntilTerminal"] is True,
        "task polling is no longer required",
    )
    require(
        contract["workflow"]["collectionOrderIsSemanticallyInsignificant"]
        is True,
        "collection order rule changed",
    )


def assert_public_types_and_validation() -> None:
    for error_type in (
        ApiError,
        ProtocolError,
        TaskFailedError,
        PollTimeoutError,
    ):
        require(
            issubclass(error_type, RuntimeError),
            f"{error_type.__name__} is not a RuntimeError",
        )

    session = "session-" + secrets.token_urlsafe(18)
    token = "kube-" + secrets.token_urlsafe(18)
    valid = "http://127.0.0.1:9"
    invalid_origins = [
        "/relative",
        "ftp://127.0.0.1",
        "http://user:pass@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1/?query=1",
        "http://127.0.0.1/#fragment",
        "http://127.0.0.1:99999",
    ]
    for origin in invalid_origins:
        require_raises(
            ValueError,
            lambda value=origin: NamespaceBackupClient(
                value, valid, session, token
            ),
            f"invalid vCenter origin accepted: {origin}",
        )
        require_raises(
            ValueError,
            lambda value=origin: NamespaceBackupClient(
                valid, value, session, token
            ),
            f"invalid Kubernetes origin accepted: {origin}",
        )

    for bad_credential in ["", "   ", "bad\nvalue", "bad\rvalue"]:
        require_raises(
            ValueError,
            lambda value=bad_credential: NamespaceBackupClient(
                valid, valid, value, token
            ),
            "unsafe vCenter credential accepted",
        )
        require_raises(
            ValueError,
            lambda value=bad_credential: NamespaceBackupClient(
                valid, valid, session, value
            ),
            "unsafe Kubernetes credential accepted",
        )

    for bad_timeout in [True, 0, -1, math.inf, math.nan]:
        require_raises(
            ValueError,
            lambda value=bad_timeout: NamespaceBackupClient(
                valid, valid, session, token, timeout=value
            ),
            "invalid timeout accepted",
        )
    for bad_interval in [True, -0.1, math.inf, math.nan]:
        require_raises(
            ValueError,
            lambda value=bad_interval: NamespaceBackupClient(
                valid, valid, session, token, poll_interval=value
            ),
            "invalid poll interval accepted",
        )
    for bad_limit in [True, 0, -1, 1.5]:
        require_raises(
            ValueError,
            lambda value=bad_limit: NamespaceBackupClient(
                valid, valid, session, token, max_polls=value
            ),
            "invalid max_polls accepted",
        )

    client = NamespaceBackupClient(
        valid + "/",
        valid + "/",
        session,
        token,
    )
    for bad_namespace in ["", "   ", None, 7]:
        require_raises(
            ValueError,
            lambda value=bad_namespace: client.backup_namespace(value),
            "invalid namespace was not rejected before traffic",
        )
    for bad_comment in [False, 8, {}, []]:
        require_raises(
            ValueError,
            lambda value=bad_comment: client.backup_namespace(
                "valid-namespace", comment=value
            ),
            "non-string comment was not rejected before traffic",
        )


def assert_common_get(record: dict[str, Any]) -> None:
    headers = header_map(record)
    require(record["method"] == "GET", "read operation did not use GET")
    require("?" not in record["raw_target"], "GET included a query or bare ?")
    require(record["body_length"] == 0, "GET included a request body")
    require(
        base64.b64decode(record["body_base64"], validate=True) == b"",
        "GET body log is inconsistent",
    )
    require("content-type" not in headers, "GET included Content-Type")
    require("content-length" not in headers, "GET included Content-Length")
    require("transfer-encoding" not in headers, "GET included Transfer-Encoding")


def assert_auth(
    record: dict[str, Any],
    *,
    session: str,
    token: str,
    kubernetes: bool,
) -> None:
    headers = header_map(record)
    require(headers.get("accept") == ["application/json"], "wrong Accept header")
    if kubernetes:
        require(
            headers.get("authorization") == [f"Bearer {token}"],
            "wrong Kubernetes Authorization header",
        )
        require(
            "vmware-api-session-id" not in headers,
            "vCenter credential leaked to Kubernetes",
        )
    else:
        require(
            headers.get("vmware-api-session-id") == [session],
            "wrong vCenter session header",
        )
        require(
            "authorization" not in headers,
            "Kubernetes credential leaked to vCenter",
        )


def cluster_fixture() -> list[dict[str, str]]:
    suffix = secrets.token_hex(5)
    return [
        {
            "name": "z-" + suffix,
            "topologyVersion": "v1.33." + str(secrets.randbelow(8) + 1),
        },
        {
            "name": "a-" + suffix,
            "topologyVersion": "v1.32." + str(secrets.randbelow(8) + 1),
        },
        {
            "name": "m-" + suffix,
            "topologyVersion": "v1.31." + str(secrets.randbelow(8) + 1),
        },
    ]


def run_primary_case(directory: Path) -> None:
    session = "session-" + secrets.token_urlsafe(24)
    token = "kube-" + secrets.token_urlsafe(24)
    namespace = "team " + secrets.token_urlsafe(7) + "/café"
    supervisor = "supervisor/" + secrets.token_urlsafe(10)
    task_id = "task/" + secrets.token_urlsafe(16)
    comment = "protected " + secrets.token_urlsafe(7) + " café"
    clusters = cluster_fixture()
    task_result = {
        "archive": "backup-" + secrets.token_urlsafe(12),
        "verified": True,
    }
    log_path = directory / "primary.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        namespace=namespace,
        supervisor=supervisor,
        clusters=clusters,
        task_id=task_id,
        task_states=["PENDING", "RUNNING", "BLOCKED", "SUCCEEDED"],
        task_result=task_result,
    ) as mock:
        client = NamespaceBackupClient(
            mock.base_url + "/",
            mock.base_url,
            session,
            token,
            timeout=3.0,
            poll_interval=0.0,
            max_polls=6,
        )
        outcome = client.backup_namespace(namespace, comment=comment)
        served_orders = mock.collection_orders

    expected_clusters = sorted(clusters, key=lambda item: item["name"])
    require(type(outcome) is dict, "success result must be a plain dict")
    require(
        set(outcome) == {"namespace", "supervisor", "clusters", "backup"},
        "success result has the wrong top-level shape",
    )
    require(outcome["namespace"] == namespace, "namespace was not preserved")
    require(outcome["supervisor"] == supervisor, "Supervisor was not preserved")
    require(
        outcome["clusters"] == expected_clusters,
        "VKS Cluster output is not name-sorted and stable",
    )
    require(
        [item["name"] for item in outcome["clusters"]]
        == sorted(item["name"] for item in clusters),
        "Cluster output did not use ordinal name ordering",
    )
    backup = outcome["backup"]
    require(type(backup) is dict, "backup result is not a plain dict")
    require(
        set(backup)
        == {
            "operationId",
            "taskOperationId",
            "taskId",
            "status",
            "pollCount",
            "result",
        },
        "backup result has the wrong shape",
    )
    require(
        backup["operationId"] == CREATE_BACKUP_OPERATION,
        "backup operationId is wrong",
    )
    require(
        backup["taskOperationId"] == GET_TASK_OPERATION,
        "task operationId is wrong",
    )
    require(backup["taskId"] == task_id, "task identifier was not preserved")
    require(backup["status"] == "SUCCEEDED", "wrong terminal task status")
    require(backup["pollCount"] == 4, "nonterminal task states were skipped")
    require(backup["result"] == task_result, "terminal result was not preserved")

    require(len(served_orders) == 2, "workflow did not read two collections")
    require(
        served_orders[1] == list(reversed(served_orders[0])),
        "mock did not flip collection element order",
    )
    canonical_names = sorted(item["name"] for item in clusters)
    require(
        served_orders[0] != canonical_names
        and served_orders[1] != canonical_names,
        "sorting check fixture accidentally served sorted output",
    )

    records = read_log(log_path)
    expected_names = (
        ["getSupervisorNamespace", "listVksClusters", "createSupervisorBackup"]
        + ["getTask"] * 4
        + ["listVksClusters"]
    )
    require(len(records) == 8, "primary workflow request count is wrong")
    require(
        [record["sequence"] for record in records] == list(range(1, 9)),
        "request log sequence is inconsistent",
    )
    require(
        [record["contract_name"] for record in records] == expected_names,
        "operation order does not prove terminal-before-second-inventory",
    )
    require(
        [record["operation_id"] for record in records]
        == [
            GET_NAMESPACE_OPERATION,
            None,
            CREATE_BACKUP_OPERATION,
            GET_TASK_OPERATION,
            GET_TASK_OPERATION,
            GET_TASK_OPERATION,
            GET_TASK_OPERATION,
            None,
        ],
        "vCenter operationId log is wrong",
    )
    require(
        [record["operation_key"] for record in records]
        == [
            None,
            LIST_CLUSTERS_OPERATION,
            None,
            None,
            None,
            None,
            None,
            LIST_CLUSTERS_OPERATION,
        ],
        "Kubernetes operation-key log is wrong",
    )

    namespace_target = (
        "/api/vcenter/namespaces/instances/v2/"
        + quote(namespace, safe="")
    )
    collection_target = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        + quote(namespace, safe="")
        + "/clusters"
    )
    backup_target = (
        "/api/vcenter/namespace-management/supervisors/"
        + quote(supervisor, safe="")
        + "/recovery/backup/jobs"
    )
    task_target = "/api/cis/tasks/" + quote(task_id, safe="")
    require(records[0]["raw_target"] == namespace_target, "wrong namespace target")
    require(records[1]["raw_target"] == collection_target, "wrong collection target")
    require(records[2]["raw_target"] == backup_target, "wrong backup target")
    for poll in records[3:7]:
        require(poll["raw_target"] == task_target, "wrong task poll target")
    require(records[7]["raw_target"] == collection_target, "wrong final target")

    for index, record in enumerate(records):
        kubernetes = index in {1, 7}
        assert_auth(
            record,
            session=session,
            token=token,
            kubernetes=kubernetes,
        )
        if record["method"] == "GET":
            assert_common_get(record)

    submit = records[2]
    submit_headers = header_map(submit)
    require(submit["method"] == "POST", "backup submission did not use POST")
    require(
        submit_headers.get("content-type") == ["application/json"],
        "backup submission has the wrong Content-Type",
    )
    require(
        "transfer-encoding" not in submit_headers,
        "backup submission used Transfer-Encoding",
    )
    expected_body = json.dumps(
        {"comment": comment},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    actual_body = base64.b64decode(
        submit["body_base64"],
        validate=True,
    )
    require(actual_body == expected_body, "backup body bytes are not exact")
    require(
        submit["body_length"] == len(expected_body),
        "backup body length is inconsistent",
    )
    require(
        submit_headers.get("content-length") == [str(len(expected_body))],
        "backup Content-Length is inconsistent",
    )
    require(
        list(json.loads(actual_body)) == ["comment"],
        "backup body contains fields outside the focused request",
    )


def run_unset_comment_case(directory: Path) -> None:
    session = "session-" + secrets.token_urlsafe(18)
    token = "kube-" + secrets.token_urlsafe(18)
    namespace = "namespace-" + secrets.token_urlsafe(7)
    supervisor = "supervisor-" + secrets.token_urlsafe(7)
    clusters = cluster_fixture()
    log_path = directory / "unset-comment.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        namespace=namespace,
        supervisor=supervisor,
        clusters=clusters,
        task_id="task-" + secrets.token_urlsafe(10),
        task_states=["SUCCEEDED"],
        task_result=None,
    ) as mock:
        outcome = NamespaceBackupClient(
            mock.base_url,
            mock.base_url,
            session,
            token,
            max_polls=1,
        ).backup_namespace(namespace)

    records = read_log(log_path)
    require(len(records) == 5, "terminal-first workflow request count is wrong")
    require(
        base64.b64decode(records[2]["body_base64"], validate=True) == b"{}",
        "unset comment was not represented by the exact empty object",
    )
    require(outcome["backup"]["pollCount"] == 1, "terminal-first poll count is wrong")
    require(
        outcome["clusters"] == sorted(clusters, key=lambda item: item["name"]),
        "unset-comment case did not sort collection output",
    )


def run_failed_task_case(directory: Path) -> None:
    session = "session-" + secrets.token_urlsafe(18)
    token = "kube-" + secrets.token_urlsafe(18)
    namespace = "namespace-" + secrets.token_urlsafe(7)
    task_id = "task-" + secrets.token_urlsafe(10)
    hidden = {
        "sensitive": "failure-" + secrets.token_urlsafe(20),
    }
    log_path = directory / "failed-task.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        namespace=namespace,
        supervisor="supervisor-" + secrets.token_urlsafe(7),
        clusters=cluster_fixture(),
        task_id=task_id,
        task_states=["FAILED"],
        task_result=None,
        task_error=hidden,
    ) as mock:
        client = NamespaceBackupClient(
            mock.base_url,
            mock.base_url,
            session,
            token,
            max_polls=3,
        )
        error = require_raises(
            TaskFailedError,
            lambda: client.backup_namespace(namespace),
            "FAILED task was not rejected",
        )

    require(getattr(error, "task_id", None) == task_id, "failed task id missing")
    require(
        getattr(error, "task_info", {}).get("error") == hidden,
        "failed task information missing",
    )
    rendered = repr(error) + str(error)
    require(session not in rendered, "failure exposed vCenter credential")
    require(token not in rendered, "failure exposed Kubernetes credential")
    require(
        hidden["sensitive"] not in rendered,
        "failure exposed task error content",
    )
    records = read_log(log_path)
    require(len(records) == 4, "FAILED task issued extra requests")
    require(
        [record["contract_name"] for record in records]
        == [
            "getSupervisorNamespace",
            "listVksClusters",
            "createSupervisorBackup",
            "getTask",
        ],
        "FAILED task incorrectly performed a post-task inventory",
    )


def run_poll_timeout_case(directory: Path) -> None:
    session = "session-" + secrets.token_urlsafe(18)
    token = "kube-" + secrets.token_urlsafe(18)
    namespace = "namespace-" + secrets.token_urlsafe(7)
    task_id = "task-" + secrets.token_urlsafe(10)
    log_path = directory / "poll-timeout.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        namespace=namespace,
        supervisor="supervisor-" + secrets.token_urlsafe(7),
        clusters=cluster_fixture(),
        task_id=task_id,
        task_states=["RUNNING"],
        task_result=None,
    ) as mock:
        client = NamespaceBackupClient(
            mock.base_url,
            mock.base_url,
            session,
            token,
            max_polls=2,
        )
        error = require_raises(
            PollTimeoutError,
            lambda: client.backup_namespace(namespace),
            "poll exhaustion was not reported",
        )

    require(getattr(error, "task_id", None) == task_id, "timeout task id missing")
    require(getattr(error, "max_polls", None) == 2, "timeout poll limit missing")
    records = read_log(log_path)
    require(len(records) == 5, "poll timeout request count is wrong")
    require(
        [record["contract_name"] for record in records]
        == [
            "getSupervisorNamespace",
            "listVksClusters",
            "createSupervisorBackup",
            "getTask",
            "getTask",
        ],
        "poll timeout over-polled or performed a final inventory",
    )


def main() -> None:
    assert_provenance()
    assert_public_types_and_validation()
    with tempfile.TemporaryDirectory(prefix="vcf91-0146-") as raw_directory:
        directory = Path(raw_directory)
        run_primary_case(directory)
        run_unset_comment_case(directory)
        run_failed_task_case(directory)
        run_poll_timeout_case(directory)
    print("PASS: namespace Supervisor backup workflow verified")


if __name__ == "__main__":
    main()
