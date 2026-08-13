#!/usr/bin/env python3
"""Protected verifier for the vcfa_catalog package.

Assertions here trace to docs/contract.json or to the task's explicit public API
and CLI requirements. The tests drive the real client against the loopback mock
in mock/vcfa_mock.py and then read the mock's request log to check the exact wire
shape that was produced. No live VMware endpoint is contacted.

Run:
    python3 -m unittest discover -s tests -t . -v
"""

import json
import ast
import importlib.util
import inspect
import os
import queue
import subprocess
import sys
import sysconfig
import threading
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
MOCK = os.path.join(ROOT, "mock", "vcfa_mock.py")
DATASET = os.path.join(ROOT, "mock", "dataset.json")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TOKEN = "vcfa-test-token"
ACCEPT = "application/json;version=9.1.0"
LIST_PATH = "/catalog/api/items"
STARTUP_TIMEOUT = 30.0

with open(DATASET, encoding="utf-8") as _fh:
    _RAW = json.load(_fh)
ITEMS = _RAW["items"]
BY_ID = {item["id"]: item for item in ITEMS}
SERVER_ORDER = _RAW["serverOrder"]

# Contract: pagination.result_ordering -- ascending by name, ties by id, on raw
# code points.
EXPECTED_ORDER = [
    item["id"] for item in sorted(ITEMS, key=lambda i: (i["name"], i["id"]))
]


def expected_ids(predicate):
    return [i for i in EXPECTED_ORDER if predicate(BY_ID[i])]


class MockServer:
    """Starts mock/vcfa_mock.py on an ephemeral loopback port."""

    def __init__(self, tmpdir, name, token=TOKEN):
        self.log_path = os.path.join(tmpdir, "requests-%s.jsonl" % name)
        self.token = token
        self.proc = None
        self.base_url = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                MOCK,
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--log",
                self.log_path,
                "--token",
                self.token,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        line = self._read_ready_line()
        parts = line.split()
        if len(parts) != 3 or parts[0] != "READY":
            raise AssertionError("mock did not announce readiness, got %r" % line)
        self.base_url = "http://%s:%s" % (parts[1], parts[2])
        return self

    def _read_ready_line(self):
        result = queue.Queue(1)

        def reader():
            try:
                result.put(self.proc.stdout.readline())
            except Exception as exc:  # pragma: no cover - defensive
                result.put("ERROR %r" % (exc,))

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            return result.get(timeout=STARTUP_TIMEOUT).strip()
        except queue.Empty:
            self.proc.kill()
            raise AssertionError("mock did not start within %.0fs" % STARTUP_TIMEOUT)

    def __exit__(self, *exc_info):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self.proc.kill()
                self.proc.wait(timeout=15)
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    stream.close()
        return False

    def requests(self):
        with open(self.log_path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


class ContractTestCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        import tempfile

        try:
            import vcfa_catalog  # noqa: F401
        except ImportError as exc:
            raise AssertionError(
                "cannot import the vcfa_catalog package from %s: %s" % (ROOT, exc)
            )

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def server(self, name, token=TOKEN):
        srv = MockServer(self._tmp.name, name, token=token)
        srv.__enter__()
        self.addCleanup(srv.__exit__)
        return srv

    def client(self, srv, **kwargs):
        from vcfa_catalog import CatalogClient

        return CatalogClient(srv.base_url, TOKEN, **kwargs)

    # -- shared assertions -------------------------------------------------

    def assert_names(self, entry, expected):
        names = [pair[0] for pair in entry["query_pairs"]]
        self.assertEqual(
            sorted(names),
            sorted(expected),
            "query parameter names for %s?%s" % (entry["path"], entry["raw_query"]),
        )
        self.assertEqual(
            len(names), len(set(names)), "a query parameter was repeated: %r" % names
        )

    def values_of(self, entry):
        return dict(entry["query_pairs"])

    def assert_all_accepted(self, srv):
        for entry in srv.requests():
            self.assertEqual(
                entry["status"],
                200,
                "the mock rejected a request: %s %s?%s -> %s"
                % (
                    entry["method"],
                    entry["path"],
                    entry["raw_query"],
                    entry["rejection"],
                ),
            )

    def assert_headers(self, srv):
        for entry in srv.requests():
            self.assertEqual(
                entry["headers"]["authorization"],
                "Bearer " + TOKEN,
                "contract auth: every request carries the bearer token",
            )
            self.assertEqual(
                entry["headers"]["accept"],
                ACCEPT,
                "contract conventions.accept_header: the API version is pinned in "
                "the Accept header",
            )


class TestPublicApi(ContractTestCase):
    def test_declared_constructor_and_method_signatures(self):
        from vcfa_catalog import CatalogClient, VcfaApiError

        constructor = inspect.signature(CatalogClient.__init__).parameters
        self.assertEqual(
            list(constructor),
            ["self", "base_url", "token", "api_version", "page_size", "timeout"],
        )
        self.assertEqual(constructor["api_version"].default, "9.1.0")
        self.assertEqual(constructor["page_size"].default, 20)
        self.assertEqual(constructor["timeout"].default, 30.0)
        self.assertEqual(
            list(inspect.signature(CatalogClient.list_catalog_items).parameters),
            [
                "self",
                "search",
                "projects",
                "types",
                "sort",
                "expand",
                "expand_projects",
            ],
        )
        self.assertEqual(
            list(inspect.signature(CatalogClient.get_catalog_item).parameters),
            ["self", "item_id", "expand", "expand_projects"],
        )
        self.assertTrue(issubclass(VcfaApiError, Exception))


class TestCollectionRetrieval(ContractTestCase):
    def test_full_collection_is_complete_deduplicated_and_stably_ordered(self):
        srv = self.server("full")
        items = self.client(srv).list_catalog_items()

        self.assertIsInstance(items, list)
        ids = [item["id"] for item in items]
        self.assertEqual(
            len(ids), len(set(ids)), "contract pagination.id_stability: de-duplicate by id"
        )
        self.assertEqual(
            len(items),
            len(ITEMS),
            "the whole collection must be retrieved; the server serves %d entries "
            "across the page walk but only %d are distinct"
            % (len(SERVER_ORDER), len(ITEMS)),
        )
        self.assertEqual(
            ids,
            EXPECTED_ORDER,
            "contract pagination.result_ordering: ascending by name, ties by id, "
            "compared on raw code points",
        )
        for item in items:
            self.assertEqual(item["name"], BY_ID[item["id"]]["name"])
        self.assert_all_accepted(srv)

    def test_ordering_is_code_point_not_case_folded(self):
        srv = self.server("codepoints")
        names = [i["name"] for i in self.client(srv).list_catalog_items()]
        upper = [n for n in names if n[:1].isupper()]
        lower = [n for n in names if n[:1].islower()]
        self.assertTrue(upper and lower, "fixture must contain both cases")
        self.assertLess(
            names.index(upper[-1]),
            names.index(lower[0]),
            "contract pagination.result_ordering: no case folding, so every "
            "upper-case initial sorts before every lower-case initial",
        )

    def test_tied_names_are_broken_by_ascending_id(self):
        srv = self.server("ties")
        items = self.client(srv).list_catalog_items()
        by_name = {}
        for item in items:
            by_name.setdefault(item["name"], []).append(item["id"])
        tied = {n: v for n, v in by_name.items() if len(v) > 1}
        self.assertTrue(tied, "fixture must contain a tied name")
        for name, ids in tied.items():
            self.assertEqual(
                ids, sorted(ids), "contract pagination.result_ordering: tie on %r" % name
            )

    def test_deduplication_keeps_the_first_occurrence(self):
        srv = self.server("first-wins")
        items = self.client(srv).list_catalog_items()

        duplicate_ids = sorted(
            item_id for item_id in set(SERVER_ORDER) if SERVER_ORDER.count(item_id) > 1
        )
        self.assertTrue(duplicate_ids, "fixture must contain repeated ids")
        returned = {item["id"]: item for item in items}
        for item_id in duplicate_ids:
            self.assertEqual(
                returned[item_id]["description"],
                BY_ID[item_id]["description"],
                "contract pagination.id_stability: later page occurrences must "
                "not replace the first object",
            )


class TestPaginationWireShape(ContractTestCase):
    def test_walk_terminates_on_last_and_issues_no_further_request(self):
        srv = self.server("walk")
        self.client(srv).list_catalog_items()

        entries = srv.requests()
        self.assertEqual(
            len(entries),
            3,
            "contract pagination.termination: the walk is exactly 3 full pages and "
            "must stop on last=true. A fourth request means termination keyed off "
            "a short or empty page instead; got pages %r"
            % [self.values_of(e).get("page") for e in entries],
        )
        for index, entry in enumerate(entries):
            self.assertEqual(entry["method"], "GET")
            self.assertEqual(entry["path"], LIST_PATH)
            values = self.values_of(entry)
            self.assertEqual(
                values.get("page"),
                str(index),
                "pages must be walked in order from 0",
            )
            self.assertEqual(values.get("size"), "20", "default page size is 20")
        self.assert_all_accepted(srv)

    def test_custom_page_size_is_honoured_end_to_end(self):
        srv = self.server("size10")
        items = self.client(srv, page_size=10).list_catalog_items()

        self.assertEqual([i["id"] for i in items], EXPECTED_ORDER)
        entries = srv.requests()
        self.assertEqual(
            len(entries),
            6,
            "%d served entries at size=10 is a 6-page walk" % len(SERVER_ORDER),
        )
        for index, entry in enumerate(entries):
            values = self.values_of(entry)
            self.assertEqual(values.get("size"), "10")
            self.assertEqual(values.get("page"), str(index))
        self.assert_all_accepted(srv)

    def test_every_request_carries_the_contract_headers(self):
        srv = self.server("headers")
        self.client(srv).list_catalog_items()
        self.assertTrue(srv.requests())
        self.assert_headers(srv)

    def test_configured_api_version_is_used_in_the_accept_header(self):
        from vcfa_catalog import VcfaApiError

        srv = self.server("version")
        with self.assertRaises(VcfaApiError) as caught:
            self.client(srv, api_version="8.18.0").list_catalog_items()
        self.assertEqual(caught.exception.status_code, 406)
        entries = srv.requests()
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0]["headers"]["accept"], "application/json;version=8.18.0"
        )

    def test_only_contract_operations_are_called(self):
        srv = self.server("routes")
        client = self.client(srv)
        client.list_catalog_items()
        client.get_catalog_item(EXPECTED_ORDER[0])

        for entry in srv.requests():
            self.assertEqual(entry["method"], "GET")
            self.assertTrue(
                entry["path"] == LIST_PATH
                or entry["path"].startswith(LIST_PATH + "/"),
                "contract out_of_scope_operations: %s is not a contract route"
                % entry["path"],
            )
        self.assert_all_accepted(srv)


class TestUnsetParametersAreOmitted(ContractTestCase):
    def test_no_filters_sends_only_page_and_size(self):
        srv = self.server("minimal")
        self.client(srv).list_catalog_items()

        entries = srv.requests()
        self.assertTrue(entries)
        for entry in entries:
            self.assert_names(entry, ["page", "size"])
            for name, value in entry["query_pairs"]:
                self.assertNotIn(
                    value,
                    (None, "", "None", "null"),
                    "contract conventions.omission_of_unset_parameters: %r was sent "
                    "with an empty value in %r" % (name, entry["raw_query"]),
                )
        self.assert_all_accepted(srv)

    def test_forbidden_odata_parameters_are_never_sent(self):
        srv = self.server("odata")
        self.client(srv).list_catalog_items()
        for entry in srv.requests():
            for name, _ in entry["query_pairs"]:
                self.assertNotIn(
                    name,
                    ("$top", "$skip", "$orderby", "$filter"),
                    "contract conventions.collection_query_style: page/size only",
                )

    def test_empty_collection_argument_counts_as_unset(self):
        srv = self.server("emptylists")
        items = self.client(srv).list_catalog_items(projects=[], types=[])

        self.assertEqual([i["id"] for i in items], EXPECTED_ORDER)
        for entry in srv.requests():
            self.assert_names(entry, ["page", "size"])
        self.assert_all_accepted(srv)

    def test_explicit_false_boolean_is_sent_rather_than_omitted(self):
        srv = self.server("falsebool")
        items = self.client(srv).list_catalog_items(expand_projects=False)

        entries = srv.requests()
        self.assertTrue(entries)
        for entry in entries:
            self.assert_names(entry, ["page", "size", "expandProjects"])
            self.assertEqual(
                self.values_of(entry)["expandProjects"],
                "false",
                "contract conventions.boolean_parameter_serialization: lowercase "
                "JSON literal",
            )
        for item in items:
            self.assertNotIn("projects", item)
        self.assert_all_accepted(srv)


class TestSuppliedFilterWireShape(ContractTestCase):
    def test_filters_are_serialised_in_contract_form(self):
        srv = self.server("filters")
        projects = [
            "a4f1c0d2-1b34-4a55-8c66-9d77e88f9a01",
            "b5e2d1c3-2c45-4b66-9d77-ae88f99a0b12",
        ]
        items = self.client(srv).list_catalog_items(
            search="cluster", projects=projects, expand_projects=True
        )

        wanted = set(projects)
        expected = expected_ids(
            lambda i: (
                "cluster" in i["name"].casefold()
                or "cluster" in i["description"].casefold()
            )
            and wanted & set(i["projectIds"])
        )
        self.assertTrue(expected, "fixture must yield a non-empty filtered result")
        self.assertEqual([i["id"] for i in items], expected)

        entries = srv.requests()
        self.assertTrue(entries)
        for entry in entries:
            self.assert_names(
                entry, ["page", "size", "search", "projects", "expandProjects"]
            )
            values = self.values_of(entry)
            self.assertEqual(values["search"], "cluster")
            self.assertEqual(
                values["projects"],
                ",".join(projects),
                "contract conventions.array_parameter_serialization: one occurrence, "
                "comma-joined, no whitespace",
            )
            self.assertEqual(values["expandProjects"], "true")
            self.assertEqual(
                entry["raw_query"].count("projects="),
                1,
                "the projects parameter must not be repeated",
            )
        self.assert_all_accepted(srv)

    def test_types_filter_is_comma_joined(self):
        srv = self.server("types")
        types = ["com.vmw.blueprint", "com.vmw.tf.configuration"]
        items = self.client(srv).list_catalog_items(types=types)

        self.assertEqual(
            [i["id"] for i in items],
            expected_ids(lambda i: i["type"]["id"] in set(types)),
        )
        for entry in srv.requests():
            self.assert_names(entry, ["page", "size", "types"])
            self.assertEqual(self.values_of(entry)["types"], ",".join(types))
        self.assert_all_accepted(srv)

    def test_sort_and_expand_are_forwarded_in_contract_form(self):
        srv = self.server("sort-expand")
        items = self.client(srv).list_catalog_items(
            sort=["name,asc", "id,desc"], expand="spec,user"
        )

        self.assertEqual([i["id"] for i in items], EXPECTED_ORDER)
        for item in items:
            self.assertEqual(item["spec"]["catalogItemId"], item["id"])
            self.assertIn("creator", item)
            self.assertIn("lastUpdater", item)
        for entry in srv.requests():
            self.assert_names(entry, ["page", "size", "sort", "expand"])
            values = self.values_of(entry)
            self.assertEqual(values["sort"], "name,asc,id,desc")
            self.assertEqual(values["expand"], "spec,user")
            self.assertNotIn("%2C", entry["raw_query"].upper())
        self.assert_all_accepted(srv)

    def test_expand_projects_true_returns_expanded_projects(self):
        srv = self.server("expand")
        items = self.client(srv).list_catalog_items(expand_projects=True)

        self.assertEqual([i["id"] for i in items], EXPECTED_ORDER)
        for item in items:
            self.assertIn("projects", item)
            self.assertEqual(
                [p["id"] for p in item["projects"]], item["projectIds"]
            )
        self.assert_all_accepted(srv)

    def test_search_value_is_percent_encoded(self):
        srv = self.server("encoding")
        self.client(srv).list_catalog_items(search="Windows Server")
        for entry in srv.requests():
            self.assertNotIn(
                " ", entry["raw_query"], "query values must be percent-encoded"
            )
            self.assertEqual(self.values_of(entry)["search"], "Windows Server")
        self.assert_all_accepted(srv)


class TestSingleItemOperation(ContractTestCase):
    def test_get_catalog_item_round_trip(self):
        srv = self.server("getone")
        target = EXPECTED_ORDER[7]
        item = self.client(srv).get_catalog_item(target)

        self.assertEqual(item["id"], target)
        self.assertEqual(item["name"], BY_ID[target]["name"])
        entries = srv.requests()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], LIST_PATH + "/" + target)
        self.assert_names(entries[0], [])
        self.assert_all_accepted(srv)
        self.assert_headers(srv)

    def test_get_catalog_item_forwards_expand_and_explicit_false(self):
        srv = self.server("get-expanded")
        target = EXPECTED_ORDER[9]
        item = self.client(srv).get_catalog_item(
            target, expand="spec,user", expand_projects=False
        )

        self.assertEqual(item["spec"]["catalogItemId"], target)
        self.assertIn("creator", item)
        self.assertIn("lastUpdater", item)
        self.assertNotIn("projects", item)
        entries = srv.requests()
        self.assertEqual(len(entries), 1)
        self.assert_names(entries[0], ["expandProjects", "expand"])
        self.assertEqual(self.values_of(entries[0])["expandProjects"], "false")
        self.assertEqual(self.values_of(entries[0])["expand"], "spec,user")
        self.assertNotIn("%2C", entries[0]["raw_query"].upper())
        self.assert_all_accepted(srv)


class TestErrorPropagation(ContractTestCase):
    def test_rejected_credentials_raise_rather_than_return_partial_results(self):
        from vcfa_catalog import CatalogClient, VcfaApiError

        srv = self.server("badtoken", token="a-different-token")
        client = CatalogClient(srv.base_url, TOKEN)
        with self.assertRaises(VcfaApiError) as caught:
            client.list_catalog_items()
        self.assertEqual(caught.exception.status_code, 401)

    def test_missing_item_raises_with_status(self):
        from vcfa_catalog import CatalogClient, VcfaApiError

        srv = self.server("missing")
        client = CatalogClient(srv.base_url, TOKEN)
        with self.assertRaises(VcfaApiError) as caught:
            client.get_catalog_item("00000000-0000-0000-0000-000000000000")
        self.assertEqual(caught.exception.status_code, 404)


class TestCommandLineInterface(ContractTestCase):
    def test_cli_emits_the_collection_in_stable_order(self):
        srv = self.server("cli")
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [
                sys.executable,
                "-S",
                "-m",
                "vcfa_catalog",
                "list-items",
                "--base-url",
                srv.base_url,
                "--token",
                TOKEN,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            proc.returncode, 0, "CLI failed:\n%s\n%s" % (proc.stdout, proc.stderr)
        )
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), len(ITEMS))
        emitted = []
        for line in lines:
            parts = line.split("\t")
            self.assertEqual(
                len(parts), 2, "each line is '<id>\\t<name>', got %r" % line
            )
            emitted.append(parts[0])
            self.assertEqual(parts[1], BY_ID[parts[0]]["name"])
        self.assertEqual(emitted, EXPECTED_ORDER)
        self.assert_all_accepted(srv)

    def test_list_cli_options_are_forwarded(self):
        srv = self.server("cli-filters")
        projects = [
            "a4f1c0d2-1b34-4a55-8c66-9d77e88f9a01",
            "b5e2d1c3-2c45-4b66-9d77-ae88f99a0b12",
        ]
        item_types = ["com.vmw.blueprint", "com.vmw.tf.configuration"]
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [
                sys.executable,
                "-S",
                "-m",
                "vcfa_catalog",
                "list-items",
                "--base-url",
                srv.base_url,
                "--token",
                TOKEN,
                "--page-size",
                "7",
                "--search",
                "cluster",
                "--project",
                projects[0],
                "--project",
                projects[1],
                "--type",
                item_types[0],
                "--type",
                item_types[1],
                "--expand-projects",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            proc.returncode, 0, "CLI failed:\n%s\n%s" % (proc.stdout, proc.stderr)
        )
        expected = expected_ids(
            lambda item: (
                "cluster" in item["name"].casefold()
                or "cluster" in item["description"].casefold()
            )
            and bool(set(projects) & set(item["projectIds"]))
            and item["type"]["id"] in item_types
        )
        self.assertTrue(expected, "fixture must yield filtered CLI results")
        self.assertEqual(
            [line.split("\t", 1)[0] for line in proc.stdout.splitlines()], expected
        )
        entries = srv.requests()
        self.assertTrue(entries)
        for index, entry in enumerate(entries):
            self.assert_names(
                entry,
                [
                    "page",
                    "size",
                    "search",
                    "projects",
                    "types",
                    "expandProjects",
                ],
            )
            values = self.values_of(entry)
            self.assertEqual(values["page"], str(index))
            self.assertEqual(values["size"], "7")
            self.assertEqual(values["search"], "cluster")
            self.assertEqual(values["projects"], ",".join(projects))
            self.assertEqual(values["types"], ",".join(item_types))
            self.assertEqual(values["expandProjects"], "true")
            self.assertNotIn("%2C", entry["raw_query"].upper())
        self.assert_all_accepted(srv)

    def test_get_item_cli_emits_json(self):
        srv = self.server("cli-get")
        target = EXPECTED_ORDER[12]
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [
                sys.executable,
                "-S",
                "-m",
                "vcfa_catalog",
                "get-item",
                "--base-url",
                srv.base_url,
                "--token",
                TOKEN,
                "--id",
                target,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            proc.returncode, 0, "CLI failed:\n%s\n%s" % (proc.stdout, proc.stderr)
        )
        self.assertEqual(json.loads(proc.stdout)["id"], target)
        entries = srv.requests()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], LIST_PATH + "/" + target)
        self.assert_names(entries[0], [])
        self.assert_all_accepted(srv)


class TestStandardLibraryOnly(unittest.TestCase):
    def test_package_imports_only_local_or_standard_library_modules(self):
        package = os.path.join(ROOT, "vcfa_catalog")
        self.assertTrue(os.path.isdir(package), "vcfa_catalog must be a package")
        purelib = os.path.realpath(sysconfig.get_path("purelib"))
        platlib = os.path.realpath(sysconfig.get_path("platlib"))

        imported = set()
        for dirpath, _, filenames in os.walk(package):
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(alias.name.split(".", 1)[0] for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        if node.module:
                            imported.add(node.module.split(".", 1)[0])

        for name in sorted(imported - {"vcfa_catalog"}):
            spec = importlib.util.find_spec(name)
            self.assertIsNotNone(spec, "imported module %r cannot be resolved" % name)
            origin = spec.origin
            if origin in (None, "built-in", "frozen"):
                continue
            resolved = os.path.realpath(origin)
            self.assertFalse(
                resolved == purelib or resolved.startswith(purelib + os.sep),
                "%r is a third-party import from %s" % (name, origin),
            )
            self.assertFalse(
                resolved == platlib or resolved.startswith(platlib + os.sep),
                "%r is a third-party import from %s" % (name, origin),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
