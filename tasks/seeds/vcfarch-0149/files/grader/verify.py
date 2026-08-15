#!/usr/bin/env python3
"""Deterministic acceptance verifier for vcfarch-0149."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
GRADER = ROOT / "grader"
BUILD = ROOT / "build"
ARTIFACT = BUILD / "architecture.json"


class VerificationError(AssertionError):
    pass


class SchemaError(VerificationError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read JSON {path.relative_to(ROOT)}: {exc}") from exc


class SchemaValidator:
    """Small local validator for the JSON-Schema vocabulary used by the pinned files."""

    def __init__(self, document: dict[str, Any]):
        self.document = document

    def resolve(self, reference: str) -> Any:
        if not reference.startswith("#/"):
            raise SchemaError(f"unsupported non-local schema reference {reference!r}")
        node: Any = self.document
        for token in reference[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or token not in node:
                raise SchemaError(f"unresolved schema reference {reference!r}")
            node = node[token]
        return node

    def validate(self, value: Any, schema: Any, path: str = "$") -> None:
        if isinstance(schema, bool):
            if not schema:
                raise SchemaError(f"{path}: value is forbidden by schema")
            return
        if not isinstance(schema, dict):
            raise SchemaError(f"{path}: malformed schema node")

        if "$ref" in schema:
            self.validate(value, self.resolve(schema["$ref"]), path)
            siblings = {key: item for key, item in schema.items() if key != "$ref"}
            if siblings:
                self.validate(value, siblings, path)
            return

        if value is None and schema.get("nullable") is True:
            return

        for part in schema.get("allOf", []):
            self.validate(value, part, path)

        if "anyOf" in schema:
            if not self._valid_against_any(value, schema["anyOf"], exactly_one=False, path=path):
                raise SchemaError(f"{path}: value does not satisfy anyOf")
        if "oneOf" in schema:
            if not self._valid_against_any(value, schema["oneOf"], exactly_one=True, path=path):
                raise SchemaError(f"{path}: value does not satisfy exactly one oneOf branch")

        if "not" in schema:
            try:
                self.validate(value, schema["not"], path)
            except SchemaError:
                pass
            else:
                raise SchemaError(f"{path}: value matches forbidden schema")

        if "enum" in schema and value not in schema["enum"]:
            raise SchemaError(f"{path}: {value!r} is not in enum {schema['enum']!r}")
        if "const" in schema and value != schema["const"]:
            raise SchemaError(f"{path}: expected constant {schema['const']!r}")

        expected = schema.get("type")
        if expected is not None and not self._has_type(value, expected):
            raise SchemaError(f"{path}: expected {expected}, got {type(value).__name__}")

        if isinstance(value, dict):
            self._validate_object(value, schema, path)
        elif isinstance(value, list):
            self._validate_array(value, schema, path)
        elif isinstance(value, str):
            self._validate_string(value, schema, path)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            self._validate_number(value, schema, path)

    def _valid_against_any(
        self, value: Any, choices: list[Any], *, exactly_one: bool, path: str
    ) -> bool:
        matches = 0
        for choice in choices:
            try:
                self.validate(value, choice, path)
            except SchemaError:
                continue
            matches += 1
        return matches == 1 if exactly_one else matches >= 1

    @staticmethod
    def _has_type(value: Any, expected: Any) -> bool:
        if isinstance(expected, list):
            return any(SchemaValidator._has_type(value, item) for item in expected)
        checks = {
            "null": value is None,
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        }
        if expected not in checks:
            raise SchemaError(f"unsupported schema type {expected!r}")
        return checks[expected]

    def _validate_object(self, value: dict[str, Any], schema: dict[str, Any], path: str) -> None:
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SchemaError(f"{path}: missing required property {key!r}")

        if len(value) < schema.get("minProperties", 0):
            raise SchemaError(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise SchemaError(f"{path}: too many properties")

        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        for key, item in value.items():
            matched = False
            if key in properties:
                self.validate(item, properties[key], f"{path}.{key}")
                matched = True
            for pattern, child_schema in patterns.items():
                if re.search(pattern, key):
                    self.validate(item, child_schema, f"{path}.{key}")
                    matched = True
            if not matched and "additionalProperties" in schema:
                additional = schema["additionalProperties"]
                if additional is False:
                    raise SchemaError(f"{path}: unexpected property {key!r}")
                if isinstance(additional, dict):
                    self.validate(item, additional, f"{path}.{key}")

    def _validate_array(self, value: list[Any], schema: dict[str, Any], path: str) -> None:
        if len(value) < schema.get("minItems", 0):
            raise SchemaError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(canonical) != len(set(canonical)):
                raise SchemaError(f"{path}: array items must be unique")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                self.validate(item, items, f"{path}[{index}]")
        elif isinstance(items, list):
            for index, child_schema in enumerate(items):
                if index < len(value):
                    self.validate(value[index], child_schema, f"{path}[{index}]")

    @staticmethod
    def _validate_string(value: str, schema: dict[str, Any], path: str) -> None:
        if len(value) < schema.get("minLength", 0):
            raise SchemaError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError(f"{path}: string is too long")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                raise SchemaError(f"{path}: invalid pinned regex: {exc}") from exc
            if matched is None:
                raise SchemaError(f"{path}: string does not match {schema['pattern']!r}")

    @staticmethod
    def _validate_number(value: int | float, schema: dict[str, Any], path: str) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise SchemaError(f"{path}: number must be finite")
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(f"{path}: number is above maximum")
        if isinstance(schema.get("exclusiveMinimum"), (int, float)) and not isinstance(
            schema.get("exclusiveMinimum"), bool
        ):
            if value <= schema["exclusiveMinimum"]:
                raise SchemaError(f"{path}: number is below exclusive minimum")
        if isinstance(schema.get("exclusiveMaximum"), (int, float)) and not isinstance(
            schema.get("exclusiveMaximum"), bool
        ):
            if value >= schema["exclusiveMaximum"]:
                raise SchemaError(f"{path}: number is above exclusive maximum")
        if "multipleOf" in schema:
            quotient = value / schema["multipleOf"]
            if not math.isclose(quotient, round(quotient), rel_tol=0.0, abs_tol=1e-9):
                raise SchemaError(f"{path}: number is not a multipleOf {schema['multipleOf']}")


def compile_and_run() -> None:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir()
    compile_result = subprocess.run(
        [
            "javac",
            "-encoding",
            "UTF-8",
            "-d",
            str(BUILD),
            str(ROOT / "src" / "VcfArchitectureClient.java"),
            str(ROOT / "test" / "TestMain.java"),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    if compile_result.returncode != 0:
        fail(f"Java compilation failed:\n{compile_result.stdout}")

    run_result = subprocess.run(
        [
            "java",
            "-cp",
            str(BUILD),
            "TestMain",
            str(ROOT / "fixtures" / "estate-inventory.json"),
            str(ARTIFACT),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    if run_result.returncode != 0:
        fail(f"TestMain failed:\n{run_result.stdout}")


def require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def check_sddc_architecture(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    target_version = snapshot["targetFoundationVersion"]
    if artifact.get("workflowType") != "VCF":
        fail("SddcSpec workflowType must be VCF")
    if artifact.get("version") != target_version:
        fail(f"SddcSpec version must be {target_version}")

    host_specs = artifact.get("hostSpecs")
    minimum = snapshot["minimumSupportedSingleSiteVsanHosts"]
    if not isinstance(host_specs, list) or len(host_specs) != minimum:
        fail(f"consolidated target must contain exactly {minimum} hostSpecs")
    hostnames = [require_dict(item, "hostSpec").get("hostname") for item in host_specs]
    if set(hostnames) != set(inventory["reservedTargetHosts"]):
        fail("SddcSpec hostSpecs must use all reserved target hosts")

    cluster = require_dict(artifact.get("clusterSpec"), "clusterSpec")
    pools = cluster.get("resourcePoolSpecs")
    pool_types = {
        item.get("type")
        for item in pools
        if isinstance(item, dict)
    } if isinstance(pools, list) else set()
    if not {"management", "compute"}.issubset(pool_types):
        fail("consolidated cluster needs management and compute resource pools")

    datastore = require_dict(artifact.get("datastoreSpec"), "datastoreSpec")
    vsan = require_dict(datastore.get("vsanSpec"), "datastoreSpec.vsanSpec")
    if vsan.get("failuresToTolerate") != 1:
        fail("four-host target must declare vSAN failuresToTolerate of 1")

    networks = artifact.get("networkSpecs")
    network_types = {
        item.get("networkType")
        for item in networks
        if isinstance(item, dict)
    } if isinstance(networks, list) else set()
    required_networks = {"MANAGEMENT", "VMOTION", "VSAN"}
    if not required_networks.issubset(network_types):
        fail(f"networkSpecs must include {sorted(required_networks)}")

    dvs_specs = artifact.get("dvsSpecs")
    if not isinstance(dvs_specs, list) or len(dvs_specs) != 1:
        fail("single-site consolidated target must define one distributed switch")
    mappings = require_dict(dvs_specs[0], "dvsSpecs[0]").get("vmnicsToUplinks")
    if not isinstance(mappings, list) or len(mappings) < 2:
        fail("distributed switch must map at least two physical uplinks")
    vmnics = [item.get("id") for item in mappings if isinstance(item, dict)]
    uplinks = [item.get("uplink") for item in mappings if isinstance(item, dict)]
    if (
        len(vmnics) != len(mappings)
        or not all(isinstance(item, str) and item.strip() for item in vmnics + uplinks)
        or len(set(vmnics)) < 2
        or len(set(uplinks)) < 2
    ):
        fail("distributed switch must use at least two distinct vmnics and uplinks")
    dvs_networks = require_dict(dvs_specs[0], "dvsSpecs[0]").get("networks")
    if not isinstance(dvs_networks, list) or not required_networks.issubset(set(dvs_networks)):
        fail(f"distributed switch must carry {sorted(required_networks)}")

    expected_resources = snapshot["targetResources"]
    vcenter = require_dict(artifact.get("vcenterSpec"), "vcenterSpec")
    sddc_manager = require_dict(artifact.get("sddcManagerSpec"), "sddcManagerSpec")
    nsx = require_dict(artifact.get("nsxtSpec"), "nsxtSpec")
    actual_resources = {
        "VCENTER": vcenter.get("vcenterHostname"),
        "SDDC_MANAGER": sddc_manager.get("hostname"),
        "NSX_MANAGER": nsx.get("vipFqdn"),
    }
    for component_type, actual in actual_resources.items():
        if actual not in expected_resources[component_type]:
            fail(f"{component_type} target resource does not match the pinned target")
    for label, component in (
        ("vcenterSpec", vcenter),
        ("sddcManagerSpec", sddc_manager),
        ("nsxtSpec", nsx),
    ):
        if component.get("version") != target_version:
            fail(f"{label}.version must be {target_version}")
        if component.get("useExistingDeployment") is not False:
            fail(f"{label} must describe a new side-by-side deployment")
    managers = nsx.get("nsxtManagers")
    if not isinstance(managers, list) or len(managers) != 3:
        fail("target NSX management cluster must name exactly three managers")
    manager_names = [item.get("hostname") for item in managers if isinstance(item, dict)]
    if (
        len(manager_names) != 3
        or not all(isinstance(item, str) and item.strip() for item in manager_names)
        or len(set(manager_names)) != 3
    ):
        fail("target NSX management cluster must contain three distinct named nodes")


def check_research(artifact: dict[str, Any]) -> None:
    research = require_dict(artifact.get("research"), "research")
    consulted = research.get("consulted")
    if not isinstance(consulted, list) or not consulted:
        fail("research.consulted must record at least one publication")
    for index, entry in enumerate(consulted):
        source = require_dict(entry, f"research.consulted[{index}]")
        title = source.get("title")
        url = source.get("url")
        if not isinstance(title, str) or not title.strip():
            fail(f"research.consulted[{index}].title must be nonblank")
        if not isinstance(url, str) or not url.strip():
            fail(f"research.consulted[{index}].url must be nonblank")
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            fail(f"research.consulted[{index}].url must identify an HTTPS Broadcom publication")


def check_migration_plan(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    plan = require_dict(artifact.get("migrationPlan"), "migrationPlan")
    plan_schema = load_json(GRADER / "migration-plan.schema.json")
    SchemaValidator(plan_schema).validate(plan, plan_schema)

    if plan["estateId"] != inventory["estateId"]:
        fail("migrationPlan estateId does not match the fixture")
    if plan["strategy"] != snapshot["strategy"]:
        fail("migrationPlan does not route around the blocked in-place path")
    if plan["targetFoundationVersion"] != snapshot["targetFoundationVersion"]:
        fail("migrationPlan target foundation version is incorrect")

    components = {item["id"]: item for item in inventory["components"]}
    steps = plan["steps"]
    step_ids = [step["componentId"] for step in steps]
    if len(step_ids) != len(set(step_ids)):
        fail("each estate component must appear exactly once in the migration plan")
    if set(step_ids) != set(components):
        missing = sorted(set(components) - set(step_ids))
        extra = sorted(set(step_ids) - set(components))
        fail(f"migration plan component coverage mismatch; missing={missing}, extra={extra}")
    if [step["order"] for step in steps] != list(range(1, len(steps) + 1)):
        fail("migration steps must be stored in contiguous order")

    rules = snapshot["componentRules"]
    target_resources = snapshot["targetResources"]
    resource_use: dict[str, list[str]] = {}
    first_order_by_type: dict[str, int] = {}
    for step in steps:
        source = components[step["componentId"]]
        component_type = source["type"]
        if step["componentType"] != component_type:
            fail(f"{step['componentId']}: componentType does not match inventory")
        if step["currentVersion"] != source["version"]:
            fail(f"{step['componentId']}: currentVersion does not match inventory")
        if step["currentBuild"] != source["build"]:
            fail(f"{step['componentId']}: currentBuild does not match inventory")

        rule = rules[component_type]
        target = step["target"]
        if target["version"] != rule["targetVersion"]:
            fail(f"{step['componentId']}: target version violates compatibility snapshot")
        if target["resource"] not in target_resources[component_type]:
            fail(f"{step['componentId']}: target resource is not in the pinned design")
        resource_use.setdefault(component_type, []).append(target["resource"])
        if step["action"] not in rule["allowedActions"]:
            fail(f"{step['componentId']}: action attempts an unsupported path")
        if set(step["gates"]) != set(rule["requiredGates"]):
            fail(f"{step['componentId']}: gates must exactly match the pinned compatibility rule")

        if source["build"] in rule.get("blockedSourceBuilds", []):
            if target["version"] not in rule.get("blockedDirectTargets", []):
                fail(f"{step['componentId']}: blocked source was not routed to the pinned target")
            if "BACK_IN_TIME_DIRECT_UPGRADE_BLOCKED" not in step["gates"]:
                fail(f"{step['componentId']}: missing back-in-time compatibility gate")
        first_order_by_type.setdefault(component_type, step["order"])

    expected_hosts = set(inventory["reservedTargetHosts"])
    actual_hosts = resource_use.get("ESX_HOST", [])
    if len(actual_hosts) != len(set(actual_hosts)) or set(actual_hosts) != expected_hosts:
        fail("ESX migration steps must map one-to-one onto the four target hosts")

    for before_type, after_type in snapshot["precedence"]:
        if first_order_by_type[before_type] >= first_order_by_type[after_type]:
            fail(f"migration precedence requires {before_type} before {after_type}")


def main() -> int:
    compile_and_run()
    artifact = require_dict(load_json(ARTIFACT), "architecture artifact")
    installer_spec = load_json(GRADER / "vcf-installer-openapi.json")

    # The installer contract is deliberately the first acceptance check on output.
    sddc_schema = installer_spec["components"]["schemas"]["SddcSpec"]
    SchemaValidator(installer_spec).validate(artifact, sddc_schema)

    inventory = require_dict(load_json(ROOT / "fixtures" / "estate-inventory.json"), "inventory")
    snapshot = require_dict(load_json(GRADER / "compatibility-snapshot.json"), "snapshot")
    check_sddc_architecture(artifact, inventory, snapshot)
    check_migration_plan(artifact, inventory, snapshot)
    check_research(artifact)
    print("PASS: schema-valid four-host VCF architecture and gated side-by-side migration plan")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
