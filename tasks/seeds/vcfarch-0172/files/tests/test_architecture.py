from __future__ import annotations

import ast
import copy
import datetime
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "estate_inventory.json"
SNAPSHOT_PATH = ROOT / "compatibility_snapshot.json"
ARCHITECTURE_PATH = ROOT / "architecture.json"
SCHEMA_PATH = ROOT / "architecture.schema.json"
EXPECTED_INVENTORY_SHA256 = (
    "dd23c468a7db671853d99d16b364a29431226cfb26d37742f22e87ad42de4ace"
)
EXPECTED_SNAPSHOT_SHA256 = (
    "5241b1950b6d161da2450c74e3d2b654d3f9c4219cbf22725c195b2d3b6d8f1f"
)
EXPECTED_SCHEMA_SHA256 = (
    "b6dcde84f78516b0d5ea3c1f673cb0f5f62c993b9229eaa95d2d2cb4c99e46f5"
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def assert_matches_schema(instance, schema, root_schema=None, location="$"):
    """Validate the JSON Schema features used by architecture.schema.json."""
    root_schema = schema if root_schema is None else root_schema
    if "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/"):
            raise AssertionError(f"unsupported external schema reference {reference}")
        target = root_schema
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        assert_matches_schema(instance, target, root_schema, location)
        return

    if "const" in schema and instance != schema["const"]:
        raise AssertionError(f"{location} does not match its schema constant")

    expected_type = schema.get("type")
    type_matches = {
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int)
        and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
    }
    if expected_type and not type_matches[expected_type](instance):
        raise AssertionError(f"{location} is not a schema {expected_type}")

    if expected_type == "object":
        required = schema.get("required", [])
        missing = set(required) - set(instance)
        if missing:
            raise AssertionError(f"{location} is missing {sorted(missing)}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                child_schema = properties[key]
            elif additional is False:
                raise AssertionError(f"{location} has unexpected property {key!r}")
            elif isinstance(additional, dict):
                child_schema = additional
            else:
                continue
            assert_matches_schema(
                value, child_schema, root_schema, f"{location}.{key}"
            )

    if expected_type == "array":
        if len(instance) < schema.get("minItems", 0):
            raise AssertionError(f"{location} has too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise AssertionError(f"{location} has too many items")
        if "items" in schema:
            for index, value in enumerate(instance):
                assert_matches_schema(
                    value,
                    schema["items"],
                    root_schema,
                    f"{location}[{index}]",
                )

    if expected_type == "integer" and instance < schema.get("minimum", instance):
        raise AssertionError(f"{location} is below its schema minimum")
    if expected_type == "string" and "pattern" in schema:
        if re.fullmatch(schema["pattern"], instance) is None:
            raise AssertionError(f"{location} does not match its schema pattern")
    if expected_type == "string" and schema.get("format") == "date":
        try:
            datetime.date.fromisoformat(instance)
        except ValueError as error:
            raise AssertionError(f"{location} is not an ISO date") from error
    if expected_type == "string" and schema.get("format") == "uri":
        parsed = urlsplit(instance)
        if not parsed.scheme or not parsed.netloc:
            raise AssertionError(f"{location} is not an absolute URI")


def assert_host_capacity(architecture, snapshot):
    domain = architecture["management_domain"]
    rules = snapshot["management_domain_rules"]
    local_ftt = domain["local_failures_to_tolerate"]
    minimum = max(
        rules["minimum_vcf_hosts_per_data_site"],
        2 * local_ftt + 1,
    )
    sites = domain["data_sites"]
    if len(sites) != rules["data_site_count"]:
        raise AssertionError("wrong number of stretched data sites")
    if any(site["host_count"] < minimum for site in sites):
        raise AssertionError(
            "host count contradicts local failures-to-tolerate or the VCF minimum"
        )
    total = sum(site["host_count"] for site in sites)
    if domain["total_data_host_count"] != total:
        raise AssertionError("total data host count does not equal the site counts")
    if total < rules["data_site_count"] * minimum:
        raise AssertionError("total host count is insufficient for the stated FTT")


class ArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = load_json(INVENTORY_PATH)
        cls.snapshot = load_json(SNAPSHOT_PATH)
        if not ARCHITECTURE_PATH.is_file():
            raise AssertionError("architecture.json was not produced")
        cls.architecture = load_json(ARCHITECTURE_PATH)

    def test_pinned_inputs_and_schema_are_unchanged(self):
        self.assertEqual(
            hashlib.sha256(INVENTORY_PATH.read_bytes()).hexdigest(),
            EXPECTED_INVENTORY_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(SNAPSHOT_PATH.read_bytes()).hexdigest(),
            EXPECTED_SNAPSHOT_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
            EXPECTED_SCHEMA_SHA256,
        )

    def test_architecture_conforms_to_supplied_schema(self):
        assert_matches_schema(self.architecture, load_json(SCHEMA_PATH))

    def test_research_records_identify_broadcom_pages_and_claims(self):
        records = self.architecture["research"]
        self.assertTrue(records)
        self.assertEqual(len({item["url"] for item in records}), len(records))
        for item in records:
            self.assertTrue(item["title"].strip())
            self.assertEqual(item["publisher"], "Broadcom")
            parsed = urlsplit(item["url"])
            self.assertEqual(parsed.scheme, "https")
            self.assertTrue(
                parsed.hostname == "broadcom.com"
                or parsed.hostname.endswith(".broadcom.com")
            )
            datetime.date.fromisoformat(item["accessed_on"])
            self.assertTrue(item["claims_used"])
            self.assertTrue(all(claim.strip() for claim in item["claims_used"]))

    def test_cli_writes_the_committed_architecture_design(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            generated = Path(temp_dir) / "architecture.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vcf_architecture",
                    "--inventory",
                    str(INVENTORY_PATH),
                    "--snapshot",
                    str(SNAPSHOT_PATH),
                    "--output",
                    str(generated),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            generated_design = load_json(generated)
            for field in (
                "schema_version",
                "estate_id",
                "inventory_sha256",
                "compatibility_snapshot_id",
                "management_domain",
                "target_components",
                "migration_steps",
                "support_boundaries",
                "research",
            ):
                self.assertEqual(generated_design[field], self.architecture[field])

    def test_identity_and_complete_source_coverage(self):
        architecture = self.architecture
        self.assertEqual(architecture["schema_version"], "1.0")
        self.assertEqual(architecture["estate_id"], self.inventory["estate_id"])
        self.assertEqual(
            architecture["inventory_sha256"],
            hashlib.sha256(INVENTORY_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            architecture["compatibility_snapshot_id"],
            self.snapshot["snapshot_id"],
        )

        expected_order = self.snapshot["migration_order"]
        steps = architecture["migration_steps"]
        self.assertEqual(len(steps), len(expected_order))
        self.assertEqual([step["order"] for step in steps], [10, 20, 30])
        self.assertEqual(
            [step["source_product_id"] for step in steps], expected_order
        )
        inventory_products = {
            product["product_id"]: product
            for product in self.inventory["source_products"]
        }
        self.assertEqual(set(inventory_products), set(expected_order))

        for step in steps:
            product_id = step["source_product_id"]
            product = inventory_products[product_id]
            path = self.snapshot["paths"][product_id]
            self.assertEqual(step["source_product"], product["name"])
            self.assertEqual(step["source_version"], product["version"])
            self.assertEqual(step["source_version"], path["supported_source"])
            self.assertEqual(step["target_component"], path["target_component"])
            self.assertEqual(step["target_version"], path["target_version"])
            self.assertEqual(step["migration_mode"], path["migration_mode"])
            self.assertEqual(
                {item["item"]: item["method"] for item in step["carry_forward"]},
                path["carry_forward"],
            )
            self.assertEqual(len(step["carry_forward"]), len(path["carry_forward"]))
            self.assertEqual(set(step["abandoned"]), set(path["abandoned"]))
            self.assertEqual(len(step["abandoned"]), len(path["abandoned"]))
            self.assertEqual(set(step["gates"]), set(path["required_gates"]))
            self.assertEqual(len(step["gates"]), len(path["required_gates"]))
            accounted_for = set(path["carry_forward"]) | set(path["abandoned"])
            self.assertTrue(
                set(product["content"]).issubset(accounted_for),
                f"unaccounted content for {product_id}",
            )

    def test_support_boundaries_match_pinned_authority(self):
        actual = {
            item["source_product_id"]: item
            for item in self.architecture["support_boundaries"]
        }
        self.assertEqual(
            len(self.architecture["support_boundaries"]),
            len(self.snapshot["support_boundaries"]),
        )
        self.assertEqual(set(actual), set(self.snapshot["support_boundaries"]))
        for product_id, expected in self.snapshot["support_boundaries"].items():
            boundary = actual[product_id]
            self.assertEqual(boundary["release_line"], expected["release_line"])
            self.assertEqual(
                boundary["end_of_general_support"],
                expected["end_of_general_support"],
            )
            self.assertEqual(
                boundary["migration_complete_before"],
                expected["end_of_general_support"],
            )

    def test_stretched_domain_and_witness_placement(self):
        architecture = self.architecture
        domain = architecture["management_domain"]
        inventory_domain = self.inventory["management_domain"]
        self.assertEqual(domain["topology"], "stretched")
        self.assertEqual(
            domain["site_failures_to_tolerate"],
            inventory_domain["requirements"]["site_failures_to_tolerate"],
        )
        self.assertEqual(
            domain["local_failures_to_tolerate"],
            inventory_domain["requirements"]["local_failures_to_tolerate"],
        )
        self.assertEqual(
            domain["storage_policy"],
            inventory_domain["requirements"]["storage_policy"],
        )
        expected_sites = {
            item["site_id"]: item["available_hosts"]
            for item in inventory_domain["data_sites"]
        }
        self.assertEqual(
            {item["site_id"]: item["host_count"] for item in domain["data_sites"]},
            expected_sites,
        )
        assert_host_capacity(architecture, self.snapshot)

        expected_witness = inventory_domain["witness_site"]
        witness = domain["vsan_witness"]
        self.assertEqual(witness["site_id"], expected_witness["site_id"])
        self.assertNotIn(witness["site_id"], expected_sites)
        self.assertEqual(witness["failure_domain"], expected_witness["failure_domain"])
        self.assertFalse(witness["host_counted_as_data"])
        self.assertFalse(witness["runs_management_workloads"])
        self.assertEqual(set(witness["connected_data_sites"]), set(expected_sites))

    def test_verifier_rejects_host_count_that_contradicts_ftt(self):
        broken = copy.deepcopy(self.architecture)
        broken["management_domain"]["data_sites"][0]["host_count"] = 2
        broken["management_domain"]["total_data_host_count"] = 6
        with self.assertRaisesRegex(AssertionError, "host count contradicts"):
            assert_host_capacity(broken, self.snapshot)

    def test_target_component_sizing_and_placement(self):
        expected_sites = {
            site["site_id"]
            for site in self.inventory["management_domain"]["data_sites"]
        }
        actual = {
            component["component"]: component
            for component in self.architecture["target_components"]
        }
        self.assertEqual(
            len(self.architecture["target_components"]),
            len(self.snapshot["target_sizing"]),
        )
        self.assertEqual(set(actual), set(self.snapshot["target_sizing"]))
        target_versions = {
            path["target_component"]: path["target_version"]
            for path in self.snapshot["paths"].values()
        }
        for name, sizing in self.snapshot["target_sizing"].items():
            component = actual[name]
            self.assertEqual(component["version"], target_versions[name])
            for field in (
                "profile",
                "node_count",
                "vcpu_per_node",
                "memory_gib_per_node",
                "disk_gib_per_node",
                "site_distribution",
            ):
                self.assertEqual(component[field], sizing[field])
            self.assertEqual(set(component["site_distribution"]), expected_sites)
            self.assertEqual(
                sum(component["site_distribution"].values()),
                component["node_count"],
            )
            self.assertEqual(
                component["placement_policy"],
                "anti-affinity-across-data-sites-on-stretched-management-domain",
            )

    def test_package_uses_only_the_standard_library(self):
        allowed_local = {"vcf_architecture"}
        for source in (ROOT / "vcf_architecture").glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    module = (node.module or "").split(".")[0]
                    names = [module]
                else:
                    continue
                for name in names:
                    self.assertTrue(
                        name in sys.stdlib_module_names or name in allowed_local,
                        f"non-stdlib import {name!r} in {source.name}",
                    )
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("dependencies = []", pyproject)


if __name__ == "__main__":
    unittest.main()
