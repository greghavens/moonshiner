"""Assertions for the SDDC LCM task-inventory collector.

Three layers are checked:

1. ``docs/contract.json`` faithfully reproduces the two operations as the
   published OpenAPI document declares them.
2. ``docs/official_sources.json`` records the provenance of the bytes fetched.
3. The client's behaviour and its exact request wire shape, read back from the
   contract-pinned loopback mock's request log.

This file is protected. Read it, run it, but do not modify it.
"""

import ast
import importlib.util
import json
import os
import re
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# What the specification says. Derived from
#   vmware/vcf-api-specs @ c3f3b52c845dd967cabbc21680e893292077d5ba
#   specifications/sddc-lcm/sddc-lcm-openapi.yaml
# ---------------------------------------------------------------------------

SPEC_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_BLOB = "fa97e0975ac108c81173b5bdd4fde57f20b2e190"
SPEC_SHA256 = "158cab89bc56e1bb80b662a859499efc6ee57c1d35503d4d1f855809c213436c"
SPEC_PATH = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"

DATE_TIME = "date-time"

GET_TASKS_PARAMS = [
    {"name": "status", "in": "query", "required": False, "type": "string"},
    {"name": "type", "in": "query", "required": False, "type": "string"},
    {"name": "createdBy", "in": "query", "required": False, "type": "string"},
    {"name": "name", "in": "query", "required": False, "type": "string"},
    {"name": "description", "in": "query", "required": False, "type": "string"},
    {"name": "startTimeGt", "in": "query", "required": False, "type": "string", "format": DATE_TIME},
    {"name": "startTimeLt", "in": "query", "required": False, "type": "string", "format": DATE_TIME},
    {"name": "updateTimeGt", "in": "query", "required": False, "type": "string", "format": DATE_TIME},
    {"name": "updateTimeLt", "in": "query", "required": False, "type": "string", "format": DATE_TIME},
    {"name": "endTimeGt", "in": "query", "required": False, "type": "string", "format": DATE_TIME},
    {"name": "endTimeLt", "in": "query", "required": False, "type": "string", "format": DATE_TIME},
    {"name": "resourceId", "in": "query", "required": False, "type": "string"},
    {"name": "resourceType", "in": "query", "required": False, "type": "string"},
    {"name": "includeSystemTasks", "in": "query", "required": False, "type": "boolean", "default": False},
    {"name": "pageNumber", "in": "query", "required": False, "type": "integer"},
    {"name": "pageSize", "in": "query", "required": False, "type": "integer", "maximum": 50},
]

GET_TASK_PARAMS = [
    {"name": "taskId", "in": "path", "required": True, "type": "string", "format": "uuid"},
]

# getTasks answers 500 with PageOfTaskSummary, not ErrorResponse. Only the
# specification says so.
GET_TASKS_RESPONSES = {"200": "PageOfTaskSummary", "500": "PageOfTaskSummary"}
GET_TASK_RESPONSES = {"200": "Task", "404": "ErrorResponse", "500": "ErrorResponse"}

TASK_STATUS_ENUM = ["PENDING", "SCHEDULED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"]
PAGE_METADATA_PROPS = ["pageNumber", "pageSize", "totalElements", "totalPages"]
TASK_SUMMARY_REQUIRED = ["id"]
TASK_SUMMARY_PROPS = [
    "id", "name", "description", "status", "type", "createdBy", "updatedBy",
    "resourceId", "resourceType", "createTime", "startTime", "updateTime",
    "endTime", "correlationId", "parentTaskId", "retriable", "cancellable",
]

SERVER_URL = "https://vcf.broadcom.com/sddc-lcm"

# ---------------------------------------------------------------------------
# Expected results, given .protected/fixtures/task_pages.json
# ---------------------------------------------------------------------------

T1 = "1a2b3c4d-0001-4a1b-9c3d-000000000001"  # startTime 2026-05-10T08:00:00Z
T2 = "2b3c4d5e-0002-4a1b-9c3d-000000000002"  # startTime 2026-05-10T08:00:00Z, FAILED
T3 = "3c4d5e6f-0003-4a1b-9c3d-000000000003"  # startTime 2026-05-11T09:15:00Z
T4 = "4d5e6f70-0004-4a1b-9c3d-000000000004"  # startTime 2026-05-12T22:45:00Z, FAILED
T5 = "5e6f7081-0005-4a1b-9c3d-000000000005"  # no startTime

STABLE_ORDER = [T1, T2, T3, T4, T5]
FAILED_ORDER = [T2, T4]

START_TIME_GT = "2026-05-01T00:00:00+00:00"
ENCODED_START_TIME_GT = "2026-05-01T00%3A00%3A00%2B00%3A00"

ALL_FILTER_VALUES = {
    "status": "FAILED",
    "type": "apply",
    "createdBy": "admin",
    "name": "upgrade task",
    "description": "LCM + task",
    "startTimeGt": START_TIME_GT,
    "startTimeLt": "2026-06-01T00:00:00+00:00",
    "updateTimeGt": "2026-05-02T00:00:00+00:00",
    "updateTimeLt": "2026-06-02T00:00:00+00:00",
    "endTimeGt": "2026-05-03T00:00:00+00:00",
    "endTimeLt": "2026-06-03T00:00:00+00:00",
    "resourceId": "af6ef462-e192-4fe1-9522-67a50a2b3392",
    "resourceType": "COMPONENT",
    "includeSystemTasks": True,
}

ALTERNATE_LIST_PATH = "/contract-derived/task-inventory"
ALTERNATE_DETAIL_PATH = ALTERNATE_LIST_PATH + "/{taskId}"


class Checks:
    """Collects every failure rather than stopping at the first."""

    def __init__(self):
        self.failures = []
        self.passed = 0

    def check(self, ok, label, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failures.append(label + ((" -- " + str(detail)) if detail else ""))
        return bool(ok)

    def equal(self, actual, expected, label):
        return self.check(
            actual == expected,
            label,
            "expected %r, got %r" % (expected, actual),
        )


# ---------------------------------------------------------------------------
# Layer 1 -- the derived contract
# ---------------------------------------------------------------------------


def _param_subset(param, expected):
    """Compare only the keys the contract is required to carry."""
    return {k: param.get(k) for k in expected}


def check_contract(c, contract):
    c.check(isinstance(contract, dict), "contract.json is a JSON object")
    if not isinstance(contract, dict):
        return

    server = contract.get("server")
    c.equal(
        server.get("url") if isinstance(server, dict) else server,
        SERVER_URL,
        "contract.server.url is the spec's servers[0].url",
    )

    schemes = contract.get("securitySchemes") or {}
    bearer = schemes.get("bearerToken") if isinstance(schemes, dict) else None
    if c.check(isinstance(bearer, dict), "contract names the bearerToken security scheme"):
        c.equal(bearer.get("type"), "http", "bearerToken.type")
        c.equal(bearer.get("scheme"), "Bearer", "bearerToken.scheme")
        c.equal(bearer.get("bearerFormat"), "JWT", "bearerToken.bearerFormat")

    operations = contract.get("operations")
    if not c.check(isinstance(operations, dict), "contract.operations is an object"):
        return

    c.equal(
        sorted(operations),
        ["getTask", "getTasks"],
        "contract names exactly the getTasks and getTask operationIds",
    )

    expectations = [
        ("getTasks", "GET", "/v1/tasks", GET_TASKS_PARAMS, GET_TASKS_RESPONSES),
        ("getTask", "GET", "/v1/tasks/{taskId}", GET_TASK_PARAMS, GET_TASK_RESPONSES),
    ]

    for operation_id, method, path, params, responses in expectations:
        op = operations.get(operation_id)
        if not c.check(isinstance(op, dict), "operations.%s is an object" % operation_id):
            continue

        c.equal(op.get("method"), method, "%s.method" % operation_id)
        c.equal(op.get("path"), path, "%s.path is the spec path template" % operation_id)
        c.equal(op.get("security"), ["bearerToken"], "%s.security" % operation_id)
        c.equal(op.get("requestBody"), None, "%s.requestBody is null" % operation_id)

        got_responses = op.get("responses")
        if isinstance(got_responses, dict):
            got_responses = {str(k): v for k, v in got_responses.items()}
        c.equal(got_responses, responses, "%s.responses map status code to schema name" % operation_id)

        got_params = op.get("parameters")
        if not c.check(isinstance(got_params, list), "%s.parameters is a list" % operation_id):
            continue

        c.equal(
            [p.get("name") for p in got_params if isinstance(p, dict)],
            [p["name"] for p in params],
            "%s.parameters names, in spec order" % operation_id,
        )

        by_name = {p.get("name"): p for p in got_params if isinstance(p, dict)}
        for expected in params:
            actual = by_name.get(expected["name"])
            if actual is None:
                continue
            c.equal(
                _param_subset(actual, expected),
                expected,
                "%s parameter %r" % (operation_id, expected["name"]),
            )

    schemas = contract.get("schemas")
    if not c.check(isinstance(schemas, dict), "contract.schemas is an object"):
        return

    task_status = schemas.get("TaskStatus") or {}
    c.equal(task_status.get("enum"), TASK_STATUS_ENUM, "schemas.TaskStatus.enum in spec order")

    page_metadata = schemas.get("PageMetadata") or {}
    c.equal(
        page_metadata.get("properties"),
        PAGE_METADATA_PROPS,
        "schemas.PageMetadata.properties in spec order",
    )

    task_summary = schemas.get("TaskSummary") or {}
    c.equal(
        task_summary.get("required"),
        TASK_SUMMARY_REQUIRED,
        "schemas.TaskSummary.required -- only id is required",
    )
    c.equal(
        task_summary.get("properties"),
        TASK_SUMMARY_PROPS,
        "schemas.TaskSummary.properties in spec order",
    )


# ---------------------------------------------------------------------------
# Layer 2 -- provenance
# ---------------------------------------------------------------------------


def check_sources(c, sources):
    if not c.check(isinstance(sources, dict), "official_sources.json is a JSON object"):
        return

    repo = str(sources.get("repository") or "").rstrip("/")
    c.equal(
        repo,
        "https://github.com/vmware/vcf-api-specs",
        "official_sources.repository is the pinned GitHub repository",
    )
    c.equal(sources.get("license"), "Apache-2.0", "official_sources.license")
    c.equal(sources.get("spec_path"), SPEC_PATH, "official_sources.spec_path")
    c.equal(str(sources.get("commit_sha") or "").lower(), SPEC_COMMIT, "official_sources.commit_sha")
    c.equal(str(sources.get("blob_sha") or "").lower(), SPEC_BLOB, "official_sources.blob_sha")
    c.equal(str(sources.get("sha256") or "").lower(), SPEC_SHA256, "official_sources.sha256")
    c.equal(
        sorted(sources.get("operation_ids") or []),
        ["getTask", "getTasks"],
        "official_sources.operation_ids",
    )


# ---------------------------------------------------------------------------
# Layer 3 -- wire shape
# ---------------------------------------------------------------------------


def _query_map(entry):
    return {k: v for k, v in entry["query_pairs"]}


def _assert_common_get(c, entry, label):
    c.equal(entry["method"], "GET", "%s is a GET" % label)
    c.equal(entry["body"], "", "%s carries no request body" % label)
    c.check(
        "content-type" not in entry["headers"],
        "%s sends no Content-Type header" % label,
        "got %r" % entry["headers"].get("content-type"),
    )
    c.check(
        entry["headers"].get("authorization", "").startswith("Bearer "),
        "%s carries an Authorization: Bearer header" % label,
        "got %r" % entry["headers"].get("authorization"),
    )
    c.equal(entry["status"], 200, "%s was answered 200 by the pinned mock" % label)

    blanks = [k for k, v in entry["query_pairs"] if v == ""]
    c.check(not blanks, "%s sends no empty-valued query parameter" % label, "blank: %s" % blanks)

    c.check(
        "+" not in entry["raw_query"],
        "%s percent-encodes its query -- no bare '+'" % label,
        "raw query %r" % entry["raw_query"],
    )

    nulls = [k for k, v in entry["query_pairs"] if v in ("null", "None", "nil", "undefined")]
    c.check(not nulls, "%s sends no stringified null" % label, "got %s" % nulls)


def check_paging_run(c, log):
    """The full collect_tasks run: three pages, then two task fetches."""
    misses = [e for e in log if e["operation_id"] is None]
    c.check(not misses, "no request fell outside the contract's operations",
            "%s" % [(e["method"], e["path"]) for e in misses])

    bad = [e for e in log if e["status"] not in (200,)]
    c.check(not bad, "every request was answered 200",
            "%s" % [(e["method"], e["target"], e["status"]) for e in bad])

    list_calls = [e for e in log if e["operation_id"] == "getTasks"]
    detail_calls = [e for e in log if e["operation_id"] == "getTask"]

    c.equal(len(list_calls), 3, "getTasks was called exactly once per page (5 elements, pageSize 2)")
    c.equal(len(detail_calls), 2, "getTask was called exactly once per FAILED summary")

    c.equal(
        [_query_map(e).get("pageNumber") for e in list_calls],
        ["0", "1", "2"],
        "pages were requested in ascending order, starting at 0, stopping at totalPages",
    )

    for index, entry in enumerate(list_calls):
        label = "getTasks page %d" % index
        _assert_common_get(c, entry, label)
        c.equal(entry["path"], "/v1/tasks", "%s targets the contract path" % label)

        query = _query_map(entry)
        c.equal(
            sorted(query),
            ["createdBy", "includeSystemTasks", "pageNumber", "pageSize", "startTimeGt"],
            "%s sends exactly the supplied filters plus paging -- every unset optional omitted" % label,
        )
        c.equal(query.get("createdBy"), "admin", "%s createdBy" % label)
        c.equal(query.get("pageSize"), "2", "%s pageSize" % label)
        c.equal(
            query.get("startTimeGt"),
            START_TIME_GT,
            "%s date-time filter survives percent-encoding intact" % label,
        )
        c.check(
            "startTimeGt=" + ENCODED_START_TIME_GT in entry["raw_query"],
            "%s percent-encodes the date-time's colons and plus sign" % label,
            "raw query %r" % entry["raw_query"],
        )
        c.equal(
            query.get("includeSystemTasks"),
            "false",
            "%s serialises the boolean false as JSON 'false'" % label,
        )

    for entry, task_id in zip(detail_calls, FAILED_ORDER):
        label = "getTask %s" % task_id[:8]
        _assert_common_get(c, entry, label)
        c.equal(entry["path"], "/v1/tasks/" + task_id, "%s targets the templated path" % label)
        c.equal(entry["raw_query"], "", "%s sends no query string" % label)

    c.equal(
        [e.get("task_id") for e in detail_calls],
        FAILED_ORDER,
        "FAILED tasks were fetched in the collection's stable order",
    )


def check_omitted_default_run(c, log):
    """includeSystemTasks left unset must not appear at all."""
    list_calls = [e for e in log if e["operation_id"] == "getTasks"]
    c.equal(len(list_calls), 3, "the second run also paged the collection completely")

    for index, entry in enumerate(list_calls):
        label = "run-2 getTasks page %d" % index
        _assert_common_get(c, entry, label)
        query = _query_map(entry)
        c.equal(
            sorted(query),
            ["createdBy", "pageNumber", "pageSize"],
            "%s omits includeSystemTasks entirely -- a schema default is not a value" % label,
        )


def check_bare_run(c, log):
    """No filters at all: only the paging parameters go on the wire."""
    list_calls = [e for e in log if e["operation_id"] == "getTasks"]
    c.equal(len(list_calls), 1, "with no page_size the contract maximum (50) fetches one page")

    entry = list_calls[0]
    _assert_common_get(c, entry, "unfiltered getTasks")
    query = _query_map(entry)
    c.equal(
        sorted(query),
        ["pageNumber", "pageSize"],
        "unfiltered getTasks sends only the paging parameters",
    )
    c.equal(query.get("pageNumber"), "0", "pageNumber 0 is sent -- 0 is a value, not an absence")
    c.equal(query.get("pageSize"), "50", "pageSize defaults to the contract's documented maximum")


def check_all_filters_run(c, log):
    """Every declared non-paging filter is accepted and preserved on the wire."""
    list_calls = [e for e in log if e["operation_id"] == "getTasks"]
    c.equal(len(list_calls), 1, "all declared filters can be sent in one request")
    if not list_calls:
        return

    entry = list_calls[0]
    _assert_common_get(c, entry, "all-filter getTasks")
    query = _query_map(entry)
    expected = dict(ALL_FILTER_VALUES)
    expected["includeSystemTasks"] = "true"
    expected["pageNumber"] = "0"
    expected["pageSize"] = "50"
    c.equal(query, expected, "every declared filter is accepted and retains its value")
    c.check(
        "%20" in entry["raw_query"] and "%2B" in entry["raw_query"],
        "spaces and literal plus signs are percent-encoded without a bare '+'",
        "raw query %r" % entry["raw_query"],
    )


def check_contract_driven_run(c, log):
    """Changed contract paths must change the actual targets the client issues."""
    list_calls = [e for e in log if e["operation_id"] == "getTasks"]
    detail_calls = [e for e in log if e["operation_id"] == "getTask"]
    c.equal(len(list_calls), 1, "alternate contract list route was called once")
    c.equal(len(detail_calls), 1, "alternate contract detail route was called once")
    if list_calls:
        c.equal(
            list_calls[0]["path"],
            ALTERNATE_LIST_PATH,
            "getTasks target follows a changed contract path",
        )
    if detail_calls:
        c.equal(
            detail_calls[0]["path"],
            ALTERNATE_LIST_PATH + "/" + T2,
            "getTask target follows a changed contract path template",
        )


def check_results(c, report, listed, bare, all_filters):
    if not c.check(isinstance(report, dict), "collect_tasks returns a dict"):
        return

    tasks = report.get("tasks")
    if c.check(isinstance(tasks, list), "collect_tasks()['tasks'] is a list"):
        c.equal(len(tasks), 5, "every element of every page was collected")
        c.equal(
            [t.get("id") for t in tasks],
            STABLE_ORDER,
            "tasks are emitted in stable order (startTime, then id; no startTime last)",
        )

    details = report.get("failed_task_details")
    if c.check(isinstance(details, list), "collect_tasks()['failed_task_details'] is a list"):
        c.equal(
            [d.get("id") for d in details],
            FAILED_ORDER,
            "failed_task_details covers the FAILED tasks in stable order",
        )
        c.check(
            all(d.get("taskSummary") is not None for d in details),
            "failed_task_details holds the full Task, not the summary",
        )

    c.equal(
        [t.get("id") for t in listed],
        STABLE_ORDER,
        "list_tasks returns the same complete, stably ordered collection",
    )
    c.equal(
        [t.get("id") for t in bare],
        STABLE_ORDER,
        "list_tasks with contract-default page size returns the complete stable collection",
    )
    c.equal(
        [t.get("id") for t in all_filters],
        STABLE_ORDER,
        "list_tasks accepts every declared filter without changing collection assembly",
    )


def check_rejected_filters(c, client):
    for bad_key in ("createdby", "created_by", "bogus"):
        try:
            client.list_tasks(filters={bad_key: "x"})
        except ValueError:
            c.check(True, "list_tasks rejects undeclared filter %r" % bad_key)
        except Exception as exc:  # noqa: BLE001
            c.check(False, "list_tasks rejects undeclared filter %r" % bad_key,
                    "raised %s: %s" % (type(exc).__name__, exc))
        else:
            c.check(False, "list_tasks rejects undeclared filter %r" % bad_key, "no error raised")

    for paging_key in ("pageNumber", "pageSize"):
        try:
            client.list_tasks(filters={paging_key: 1})
        except ValueError:
            c.check(True, "list_tasks rejects %r as a filter" % paging_key)
        except Exception as exc:  # noqa: BLE001
            c.check(False, "list_tasks rejects %r as a filter" % paging_key,
                    "raised %s: %s" % (type(exc).__name__, exc))
        else:
            c.check(False, "list_tasks rejects %r as a filter" % paging_key, "no error raised")


def check_build_target(c, contract):
    try:
        bare = contract.build_target("getTasks", query={})
    except Exception as exc:  # noqa: BLE001
        c.check(False, "build_target('getTasks', query={}) works", "%s: %s" % (type(exc).__name__, exc))
    else:
        c.equal(bare, "/v1/tasks", "build_target with an empty query appends no '?'")

    try:
        templated = contract.build_target("getTask", path_params={"taskId": T2})
    except Exception as exc:  # noqa: BLE001
        c.check(False, "build_target('getTask', ...) works", "%s: %s" % (type(exc).__name__, exc))
    else:
        c.equal(templated, "/v1/tasks/" + T2, "build_target substitutes the path template")

    try:
        names = contract.query_parameters("getTasks")
    except Exception as exc:  # noqa: BLE001
        c.check(False, "query_parameters('getTasks') works", "%s: %s" % (type(exc).__name__, exc))
    else:
        c.equal(
            list(names),
            [p["name"] for p in GET_TASKS_PARAMS],
            "query_parameters reports the spec's query parameters in order",
        )

    try:
        contract.operation("getComponents")
    except KeyError:
        c.check(True, "operation() raises KeyError for an operation the contract does not name")
    except Exception as exc:  # noqa: BLE001
        c.check(False, "operation() raises KeyError for an unnamed operation",
                "raised %s" % type(exc).__name__)
    else:
        c.check(False, "operation() raises KeyError for an unnamed operation", "no error raised")


def check_no_path_literals(c):
    """The client must build targets from the contract, not from literals."""
    source = (ROOT / "src" / "vcf_sddc_lcm" / "client.py").read_text(encoding="utf-8")
    stripped = re.sub(r"#.*", "", source)
    stripped = re.sub(r'"""(?:.|\n)*?"""', "", stripped)
    stripped = re.sub(r"'''(?:.|\n)*?'''", "", stripped)
    hits = re.findall(r"""['"]/v1/[^'"]*['"]""", stripped)
    c.check(
        not hits,
        "client.py contains no hard-coded /v1/ path literal -- targets come from the contract",
        "found %s" % hits,
    )
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        c.check(False, "client.py parses for build_target inspection", str(exc))
    else:
        references = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "build_target"
        ]
        c.check(
            bool(references),
            "client.py delegates request-target construction to Contract.build_target",
        )


def _within(path, directory):
    try:
        return os.path.commonpath((str(path), str(directory))) == str(directory)
    except ValueError:
        return False


def check_standard_library_only(c):
    """Reject imports that resolve outside Python's standard library."""
    package = ROOT / "src" / "vcf_sddc_lcm"
    imported = set()
    parse_errors = []
    for source_path in sorted(package.glob("*.py")):
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        except SyntaxError as exc:
            parse_errors.append("%s: %s" % (source_path.name, exc))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".", 1)[0])

    c.check(not parse_errors, "package sources parse for dependency inspection", parse_errors)

    stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
    outside = []
    for name in sorted(imported - {"vcf_sddc_lcm"}):
        spec = importlib.util.find_spec(name)
        origin = None if spec is None else spec.origin
        if origin in ("built-in", "frozen"):
            continue
        if not origin:
            outside.append(name)
            continue
        resolved = Path(origin).resolve()
        parts = set(resolved.parts)
        if not _within(resolved, stdlib) or {"site-packages", "dist-packages"} & parts:
            outside.append(name)

    c.check(
        not outside,
        "the package imports only Python standard-library modules",
        "non-stdlib imports: %s" % outside,
    )
