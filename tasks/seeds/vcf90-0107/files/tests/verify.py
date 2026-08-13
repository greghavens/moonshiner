from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mock_vcf_installer import MockVCFInstaller


EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC = "specifications/vcf-installer/vcf-installer-openapi.json"
OPTIONAL_QUERY_NAMES = {
    "limit",
    "taskStatus",
    "taskType",
    "resourceId",
    "resourceType",
    "completedAfter",
    "taskName",
    "doLiveRefresh",
}


def task(task_id: str) -> dict[str, str]:
    return {
        "id": task_id,
        "name": f"Task {task_id}",
        "status": "SUCCESSFUL",
        "creationTimestamp": "2026-01-01T00:00:00Z",
    }


def page(
    elements: object,
    *,
    page_number: object = 0,
    page_size: object | None = None,
    total_elements: object | None = None,
    total_pages: object = 1,
) -> dict[str, object]:
    element_count = len(elements) if isinstance(elements, list) else 0
    return {
        "elements": elements,
        "pageMetadata": {
            "pageNumber": page_number,
            "pageSize": element_count if page_size is None else page_size,
            "totalElements": element_count if total_elements is None else total_elements,
            "totalPages": total_pages,
        },
    }


def scripted(document: object, status: int = 200) -> list[tuple[int, bytes]]:
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    return [(status, payload)]


class ContractFixtureTests(unittest.TestCase):
    def test_contract_and_provenance_are_pinned_to_vcf_9_0(self) -> None:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["source"]["tag"], "9.0.0.0")
        self.assertEqual(contract["source"]["commitSha"], EXPECTED_SHA)
        self.assertEqual(contract["source"]["specPath"], EXPECTED_SPEC)
        self.assertEqual(sources["tag"], "9.0.0.0")
        self.assertEqual(sources["tagCommitSha"], EXPECTED_SHA)
        self.assertEqual(sources["specPath"], EXPECTED_SPEC)
        self.assertEqual(sources["operationIds"], ["getTasks"])
        operations = contract["operations"]
        self.assertEqual(
            [(item["operationId"], item["method"], item["path"]) for item in operations],
            [("getTasks", "GET", "/v1/tasks")],
        )
        parameters = [item["name"] for item in operations[0]["parameters"]]
        self.assertEqual(
            parameters,
            [
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
            ],
        )


class PublicSurfaceTests(unittest.TestCase):
    def test_exports_and_signatures(self) -> None:
        import vcf_installer

        self.assertEqual(
            set(vcf_installer.__all__), {"VCFInstallerClient", "VCFInstallerError"}
        )
        self.assertTrue(issubclass(vcf_installer.VCFInstallerError, Exception))

        init_parameters = inspect.signature(vcf_installer.VCFInstallerClient).parameters
        self.assertEqual(list(init_parameters), ["base_url", "timeout"])
        self.assertEqual(init_parameters["timeout"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(init_parameters["timeout"].default, 10.0)

        client = object.__new__(vcf_installer.VCFInstallerClient)
        parameters = inspect.signature(client.list_all_tasks).parameters
        self.assertEqual(
            list(parameters),
            [
                "page_size",
                "task_status",
                "task_type",
                "resource_id",
                "resource_type",
                "completed_after",
                "task_name",
                "do_live_refresh",
            ],
        )
        self.assertTrue(
            all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in parameters.values())
        )
        self.assertEqual(parameters["page_size"].default, 100)
        for name in list(parameters)[1:]:
            self.assertIsNone(parameters[name].default)

    def test_package_imports_only_standard_library_or_itself(self) -> None:
        package = ROOT / "vcf_installer"
        self.assertTrue((package / "__init__.py").is_file())
        allowed = set(sys.stdlib_module_names) | {"__future__", "vcf_installer"}
        unexpected: list[str] = []
        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.partition(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = [node.module.partition(".")[0]]
                else:
                    continue
                unexpected.extend(root for root in roots if root not in allowed)
        self.assertEqual(unexpected, [])


class ClientIntegrationTests(unittest.TestCase):
    def test_complete_collection_and_unset_wire_shape(self) -> None:
        from vcf_installer import VCFInstallerClient

        with MockVCFInstaller() as mock:
            tasks = VCFInstallerClient(mock.base_url).list_all_tasks(page_size=2)
            log = list(mock.request_log)

        self.assertEqual(
            [item["id"] for item in tasks],
            ["task-a", "task-b", "task-c", "task-d", "task-e"],
        )
        self.assertEqual(len(log), 3)
        for page_number, request in enumerate(log):
            pairs = request["queryPairs"]
            query = dict(pairs)
            self.assertEqual(request["method"], "GET")
            self.assertEqual(request["path"], "/v1/tasks")
            self.assertEqual(len(pairs), len(query), "query names must not be duplicated")
            self.assertEqual(
                query,
                {
                    "pageNumber": str(page_number),
                    "pageSize": "2",
                    "orderBy": "id",
                    "orderDirection": "ASC",
                },
            )
            self.assertTrue(OPTIONAL_QUERY_NAMES.isdisjoint(query))
            self.assertEqual(request["body"], b"")

    def test_all_explicit_filters_use_contract_names_and_url_encoding(self) -> None:
        from vcf_installer import VCFInstallerClient

        with MockVCFInstaller() as mock:
            tasks = VCFInstallerClient(mock.base_url).list_all_tasks(
                page_size=7,
                task_status="SUCCESSFUL",
                task_type="HOST_DISCOVERY",
                resource_id="rack/A + west&blue",
                resource_type="ESXI",
                completed_after=1000,
                task_name="Discover",
                do_live_refresh=False,
            )
            log = list(mock.request_log)

        self.assertEqual([item["id"] for item in tasks], ["task-a"])
        self.assertEqual(len(log), 1)
        pairs = log[0]["queryPairs"]
        self.assertEqual(len(pairs), len(dict(pairs)), "query names must not be duplicated")
        self.assertEqual(
            dict(pairs),
            {
                "pageNumber": "0",
                "pageSize": "7",
                "orderBy": "id",
                "orderDirection": "ASC",
                "taskStatus": "SUCCESSFUL",
                "taskType": "HOST_DISCOVERY",
                "resourceId": "rack/A + west&blue",
                "resourceType": "ESXI",
                "completedAfter": "1000",
                "taskName": "Discover",
                "doLiveRefresh": "false",
            },
        )
        self.assertNotIn(" ", log[0]["rawQuery"])
        self.assertNotIn("limit", dict(pairs))

    def test_true_boolean_is_lowercase(self) -> None:
        from vcf_installer import VCFInstallerClient

        with MockVCFInstaller() as mock:
            VCFInstallerClient(mock.base_url).list_all_tasks(do_live_refresh=True)
            pairs = dict(mock.request_log[0]["queryPairs"])

        self.assertEqual(pairs["doLiveRefresh"], "true")

    def test_result_is_sorted_even_if_a_response_is_not(self) -> None:
        from vcf_installer import VCFInstallerClient

        document = page([task("task-c"), task("task-a"), task("task-b")])
        with MockVCFInstaller(scripted(document)) as mock:
            tasks = VCFInstallerClient(mock.base_url).list_all_tasks(page_size=3)

        self.assertEqual([item["id"] for item in tasks], ["task-a", "task-b", "task-c"])

    def test_bad_page_size_is_rejected_before_a_request(self) -> None:
        from vcf_installer import VCFInstallerClient

        for value in (0, 101, True, 1.5, "2"):
            with self.subTest(value=value), MockVCFInstaller() as mock:
                with self.assertRaises(ValueError):
                    VCFInstallerClient(mock.base_url).list_all_tasks(page_size=value)  # type: ignore[arg-type]
                self.assertEqual(mock.request_log, [])


class FailureTests(unittest.TestCase):
    def test_http_failure_raises_public_error(self) -> None:
        from vcf_installer import VCFInstallerClient, VCFInstallerError

        with MockVCFInstaller(scripted({"error": "unavailable"}, status=503)) as mock:
            with self.assertRaises(VCFInstallerError):
                VCFInstallerClient(mock.base_url).list_all_tasks()

    def test_transport_failure_raises_public_error(self) -> None:
        from vcf_installer import VCFInstallerClient, VCFInstallerError

        with MockVCFInstaller([(-1, b"")]) as mock:
            with self.assertRaises(VCFInstallerError):
                VCFInstallerClient(mock.base_url, timeout=0.5).list_all_tasks()

    def test_invalid_json_raises_public_error(self) -> None:
        from vcf_installer import VCFInstallerClient, VCFInstallerError

        with MockVCFInstaller([(200, b"not JSON")]) as mock:
            with self.assertRaises(VCFInstallerError):
                VCFInstallerClient(mock.base_url).list_all_tasks()

    def test_malformed_page_shapes_raise_public_error(self) -> None:
        from vcf_installer import VCFInstallerClient, VCFInstallerError

        malformed = {
            "non-object": [],
            "missing-metadata": {"elements": []},
            "elements-not-list": page({"id": "task-a"}),
            "element-not-object": page(["task-a"]),
            "id-not-string": page([{"id": 7}]),
            "boolean-metadata": page([], page_number=False, total_pages=0),
            "wrong-page-number": page([], page_number=1, total_pages=0),
            "wrong-page-size": page([task("task-a")], page_size=2),
            "negative-total": page([], total_elements=-1, total_pages=0),
            "zero-pages-with-elements": page([task("task-a")], total_pages=0),
            "inconsistent-page-count": page(
                [task("task-a"), task("task-b")], total_elements=5, total_pages=1
            ),
            "missing-expected-elements": page([], total_elements=1, total_pages=1),
        }
        for name, document in malformed.items():
            with self.subTest(name=name), MockVCFInstaller(scripted(document)) as mock:
                with self.assertRaises(VCFInstallerError):
                    VCFInstallerClient(mock.base_url).list_all_tasks(page_size=2)

    def test_collection_totals_may_not_change_between_pages(self) -> None:
        from vcf_installer import VCFInstallerClient, VCFInstallerError

        responses = scripted(
            page(
                [task("task-a"), task("task-b")],
                total_elements=3,
                total_pages=2,
            )
        )
        responses += scripted(
            page(
                [task("task-c"), task("task-d")],
                page_number=1,
                total_elements=4,
                total_pages=2,
            )
        )
        with MockVCFInstaller(responses) as mock:
            with self.assertRaises(VCFInstallerError):
                VCFInstallerClient(mock.base_url).list_all_tasks(page_size=2)
            self.assertEqual(len(mock.request_log), 2)


class CliTests(unittest.TestCase):
    def test_cli_emits_byte_stable_complete_json(self) -> None:
        with MockVCFInstaller() as mock:
            result = subprocess.run(
                [sys.executable, "-m", "vcf_installer", mock.base_url, "--page-size", "2"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            log = list(mock.request_log)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(log), 3)
        self.assertTrue(
            all(dict(request["queryPairs"])["pageSize"] == "2" for request in log)
        )
        decoded = json.loads(result.stdout)
        self.assertEqual(
            [item["id"] for item in decoded],
            ["task-a", "task-b", "task-c", "task-d", "task-e"],
        )
        expected = json.dumps(decoded, sort_keys=True, separators=(",", ":")) + "\n"
        self.assertEqual(result.stdout, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
