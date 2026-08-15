#!/usr/bin/env python3
"""Offline acceptance verifier for the VCF brownfield architecture."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "architecture" / "migration-plan.json"
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
PLAN_SCHEMA_PATH = ROOT / "contracts" / "migration-plan.schema.json"
SNAPSHOT_PATH = ROOT / "contracts" / "compatibility-snapshot.json"
INVENTORY_PATH = ROOT / "inventory" / "estate.json"
RESEARCH_PATH = ROOT / "architecture" / "research-consulted.md"
GENERATED_PATH = ROOT / "architecture" / ".verify-generated.json"


class VerificationError(AssertionError):
    pass


class SchemaError(VerificationError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: line {exc.lineno}: {exc.msg}"
        ) from exc


class LocalSchemaValidator:
    """Small dependency-free validator for the keywords used by the pinned schemas."""

    def __init__(self, root_schema: dict[str, Any]):
        self.root = root_schema

    def resolve(self, reference: str) -> Any:
        if not reference.startswith("#/"):
            raise SchemaError(f"external schema reference is not permitted: {reference}")
        node: Any = self.root
        for raw in reference[2:].split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            try:
                node = node[token]
            except (KeyError, TypeError) as exc:
                raise SchemaError(f"unresolvable local schema reference: {reference}") from exc
        return node

    def validate(self, value: Any, schema: Any, path: str = "$") -> None:
        if isinstance(schema, bool):
            if not schema:
                raise SchemaError(f"{path}: value rejected by false schema")
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

        if "allOf" in schema:
            for branch in schema["allOf"]:
                self.validate(value, branch, path)
        if "anyOf" in schema:
            if not any(self._accepts(value, branch, path) for branch in schema["anyOf"]):
                raise SchemaError(f"{path}: does not match any allowed schema")
        if "oneOf" in schema:
            count = sum(self._accepts(value, branch, path) for branch in schema["oneOf"])
            if count != 1:
                raise SchemaError(f"{path}: matches {count} oneOf branches, expected exactly one")
        if "not" in schema and self._accepts(value, schema["not"], path):
            raise SchemaError(f"{path}: matches a prohibited schema")

        if "const" in schema and value != schema["const"]:
            raise SchemaError(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            raise SchemaError(f"{path}: {value!r} is not one of {schema['enum']!r}")

        expected = schema.get("type")
        if expected is not None and not self._matches_type(value, expected):
            raise SchemaError(f"{path}: expected {expected}, got {type(value).__name__}")

        if isinstance(value, dict):
            required = schema.get("required", [])
            missing = [key for key in required if key not in value]
            if missing:
                raise SchemaError(f"{path}: missing required properties {missing}")
            properties = schema.get("properties", {})
            for key, item in value.items():
                child_path = f"{path}.{key}"
                if key in properties:
                    self.validate(item, properties[key], child_path)
                elif schema.get("additionalProperties") is False:
                    raise SchemaError(f"{child_path}: additional property is not allowed")
                elif isinstance(schema.get("additionalProperties"), dict):
                    self.validate(item, schema["additionalProperties"], child_path)

        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                raise SchemaError(f"{path}: too few items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                raise SchemaError(f"{path}: too many items")
            if schema.get("uniqueItems"):
                encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
                if len(encoded) != len(set(encoded)):
                    raise SchemaError(f"{path}: items must be unique")
            if "items" in schema:
                for index, item in enumerate(value):
                    self.validate(item, schema["items"], f"{path}[{index}]")

        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                raise SchemaError(f"{path}: string is shorter than minLength")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise SchemaError(f"{path}: string is longer than maxLength")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                raise SchemaError(f"{path}: string does not match {schema['pattern']!r}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise SchemaError(f"{path}: value is below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                raise SchemaError(f"{path}: value is above maximum")

    def _accepts(self, value: Any, schema: Any, path: str) -> bool:
        try:
            self.validate(value, schema, path)
            return True
        except SchemaError:
            return False

    @staticmethod
    def _matches_type(value: Any, expected: str | list[str]) -> bool:
        if isinstance(expected, list):
            return any(LocalSchemaValidator._matches_type(value, item) for item in expected)
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if expected not in checks:
            raise SchemaError(f"unsupported schema type: {expected}")
        return checks[expected](value)


def one(items: list[dict[str, Any]], description: str) -> dict[str, Any]:
    if len(items) != 1:
        raise VerificationError(f"expected exactly one {description}, found {len(items)}")
    return items[0]


def validate_research_record(
    inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationError("missing required file: architecture/research-consulted.md") from exc

    if re.search(r"(?i)consult(?:ed|ation date)\s*:?\s*20\d{2}-\d{2}-\d{2}", text) is None:
        raise VerificationError("research record does not include an ISO consultation date")

    url_pattern = re.compile(r"https://[^\s)>]+")
    all_urls = list(url_pattern.finditer(text))
    for match in all_urls:
        parsed = urlparse(match.group(0).rstrip(".,;"))
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            raise VerificationError(f"research URL is not a Broadcom HTTPS source: {match.group(0)}")
    distinct_urls = {match.group(0).rstrip(".,;") for match in all_urls}
    if len(distinct_urls) < 3:
        raise VerificationError("research record needs at least three distinct Broadcom sources")

    records = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        matches = list(url_pattern.finditer(line))
        if not matches:
            continue
        title = line[: matches[0].start()].strip(" #*|[]()—–-:")
        design_fact = line[matches[-1].end() :].strip(" —–-:")
        if len(title) >= 8 and len(design_fact) >= 30:
            records.append(line)

    # Also accept a heading/field-style Markdown record spread across several
    # lines, provided its title, URL, and design fact share one paragraph block.
    for block in re.split(r"\n\s*\n", text):
        matches = list(url_pattern.finditer(block))
        if len(matches) != 1:
            continue
        title = block[: matches[0].start()].strip(" \n#*|[]()—–-:")
        design_fact = block[matches[-1].end() :].strip(" \n#*|[]()—–-:")
        if len(title) >= 8 and len(design_fact) >= 30:
            records.append(block)

    if len(records) < 3:
        raise VerificationError(
            "research record needs at least three titled Broadcom sources with design facts"
        )

    product_labels = {
        "vcenter": r"\bvcenter\b",
        "esxi": r"\besxi\b",
        "vsan": r"\bvsan\b",
        "nsx": r"\bnsx\b",
        "live-site-recovery": r"\blive site recovery\b",
        "vsphere-replication": r"\bvsphere replication\b",
    }
    inventoried_types = {item["type"] for item in inventory["components"]}
    missing_products = sorted(
        product_type
        for product_type in inventoried_types
        if product_type not in product_labels
        or re.search(product_labels[product_type], text, re.IGNORECASE) is None
    )
    if missing_products:
        raise VerificationError(
            f"research record does not cover inventoried product types: {missing_products}"
        )

    missing_targets = sorted(
        {
            target["targetVersion"]
            for product_type, target in snapshot["componentTargets"].items()
            if product_type in inventoried_types and target["targetVersion"] not in text
        }
    )
    if missing_targets:
        raise VerificationError(
            f"research record omits pinned target versions used by the design: {missing_targets}"
        )


def validate_semantics(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    if plan["estateId"] != inventory["estateId"]:
        raise VerificationError("plan estateId does not match inventory")
    if plan["targetVcfVersion"] != inventory["targetVcfVersion"]:
        raise VerificationError("plan targetVcfVersion does not match inventory")
    if plan["targetVcfVersion"] != snapshot["targetVcfVersion"]:
        raise VerificationError("plan targetVcfVersion does not match compatibility snapshot")

    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    if len(inventory_by_id) != len(inventory["components"]):
        raise VerificationError("protected inventory has duplicate component IDs")
    plan_by_id = {item["id"]: item for item in plan["components"]}
    if len(plan_by_id) != len(plan["components"]):
        raise VerificationError("plan has duplicate component IDs")
    if set(plan_by_id) != set(inventory_by_id):
        missing = sorted(set(inventory_by_id) - set(plan_by_id))
        extra = sorted(set(plan_by_id) - set(inventory_by_id))
        raise VerificationError(f"plan component coverage differs from inventory; missing={missing}, extra={extra}")

    targets = snapshot["componentTargets"]
    for component_id, source in inventory_by_id.items():
        actual = plan_by_id[component_id]
        target = targets[source["type"]]
        for field in ("type", "site"):
            if actual[field] != source[field]:
                raise VerificationError(f"{component_id}: {field} does not match inventory")
        if actual["currentVersion"] != source["version"]:
            raise VerificationError(f"{component_id}: currentVersion does not match inventory")
        if re.search(target["acceptedSourcePattern"], source["version"]) is None:
            raise VerificationError(f"{component_id}: protected source version is outside snapshot boundary")
        for field in ("targetProduct", "targetVersion", "disposition"):
            if actual[field] != target[field]:
                raise VerificationError(f"{component_id}: {field} does not match pinned compatibility target")

    steps = plan["steps"]
    if [step["order"] for step in steps] != list(range(1, len(steps) + 1)):
        raise VerificationError("step order must be contiguous, unique, and array ordered from 1")
    steps_by_id = {step["id"]: step for step in steps}
    if len(steps_by_id) != len(steps):
        raise VerificationError("step IDs must be unique")
    order_by_id = {step["id"]: step["order"] for step in steps}
    valid_rule_ids = {rule["id"] for rule in snapshot["rules"]}
    for step in steps:
        unknown_components = set(step["componentIds"]) - set(inventory_by_id)
        if unknown_components:
            raise VerificationError(f"{step['id']}: unknown component IDs {sorted(unknown_components)}")
        unknown_rules = set(step["ruleIds"]) - valid_rule_ids
        if unknown_rules:
            raise VerificationError(f"{step['id']}: unknown compatibility rule IDs {sorted(unknown_rules)}")
        for prerequisite in step["requires"]:
            if prerequisite not in steps_by_id:
                raise VerificationError(f"{step['id']}: unknown prerequisite {prerequisite}")
            if order_by_id[prerequisite] >= step["order"]:
                raise VerificationError(f"{step['id']}: prerequisite {prerequisite} is not earlier")

    def ancestors(step_id: str) -> set[str]:
        found: set[str] = set()
        pending = list(steps_by_id[step_id]["requires"])
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            pending.extend(steps_by_id[current]["requires"])
        return found

    for component in plan["components"]:
        change_id = component["changeStep"]
        if change_id not in steps_by_id:
            raise VerificationError(f"{component['id']}: changeStep does not exist")
        change = steps_by_id[change_id]
        if component["id"] not in change["componentIds"]:
            raise VerificationError(f"{component['id']}: changeStep does not name the component")
        if change.get("targetVersion") != component["targetVersion"]:
            raise VerificationError(f"{component['id']}: changeStep targetVersion is not its pinned target")
        available_gates = ancestors(change_id)
        if not set(component["gatedBy"]).issubset(available_gates):
            raise VerificationError(f"{component['id']}: gatedBy contains a non-prerequisite step")

    all_ids = set(inventory_by_id)
    first = steps[0]
    if first["action"] != "validate" or set(first["componentIds"]) != all_ids:
        raise VerificationError("the first step must validate the complete estate")
    last = steps[-1]
    if last["action"] != "verify" or set(last["componentIds"]) != all_ids:
        raise VerificationError("the final step must verify every component in the fleet")

    constraints = snapshot["constraints"]
    core_types = set(constraints["coreTypes"])
    legacy_types = set(constraints["legacyRecoveryTypes"])
    core_ids = {
        item["id"] for item in inventory["components"] if item["type"] in core_types
    }
    legacy_ids = {
        item["id"] for item in inventory["components"] if item["type"] in legacy_types
    }

    for site in constraints["sites"]:
        site_core = {
            item["id"]
            for item in inventory["components"]
            if item["site"] == site and item["type"] in core_types
        }
        imports = [
            step for step in steps
            if step["action"] == "import" and set(step["componentIds"]) == site_core
        ]
        import_step = one(imports, f"complete {site} core import step")
        for component_id in site_core:
            if import_step["order"] >= order_by_id[plan_by_id[component_id]["changeStep"]]:
                raise VerificationError(f"{component_id}: core upgrade is not after its brownfield import")

        stage_orders: list[int] = []
        for type_stage in constraints["coreUpgradeOrder"]:
            ids = {
                item["id"]
                for item in inventory["components"]
                if item["site"] == site and item["type"] in set(type_stage)
            }
            change_ids = {plan_by_id[item]["changeStep"] for item in ids}
            if len(change_ids) != 1:
                raise VerificationError(f"{site}: {type_stage} must share one atomic change step")
            change = steps_by_id[next(iter(change_ids))]
            if change["action"] != "upgrade" or set(change["componentIds"]) != ids:
                raise VerificationError(f"{site}: invalid upgrade membership for {type_stage}")
            stage_orders.append(change["order"])
        if stage_orders != sorted(stage_orders) or len(stage_orders) != len(set(stage_orders)):
            raise VerificationError(f"{site}: core upgrades violate the pinned NSX/vCenter/ESXi-vSAN order")

    action_orders: list[int] = []
    for action in constraints["legacyRecoveryActions"]:
        matches = [
            step for step in steps
            if step["action"] == action and set(step["componentIds"]) == legacy_ids
        ]
        action_orders.append(one(matches, f"complete legacy recovery {action} step")["order"])
    if action_orders != sorted(action_orders) or len(action_orders) != len(set(action_orders)):
        raise VerificationError("legacy recovery actions are not quiesce, unregister, then remove")
    removal_order = action_orders[-1]

    vcenter_ids = {
        item["id"] for item in inventory["components"] if item["type"] == "vcenter"
    }
    if any(order_by_id[plan_by_id[item]["changeStep"]] <= removal_order for item in vcenter_ids):
        raise VerificationError("a vCenter reaches 9.1 before legacy recovery appliances are removed")

    deployment_orders: list[int] = []
    for site in constraints["sites"]:
        site_legacy = {
            item["id"]
            for item in inventory["components"]
            if item["site"] == site and item["type"] in legacy_types
        }
        change_ids = {plan_by_id[item]["changeStep"] for item in site_legacy}
        if len(change_ids) != 1:
            raise VerificationError(f"{site}: legacy products must converge in one unified deployment")
        deployment = steps_by_id[next(iter(change_ids))]
        if deployment["action"] != "deploy" or set(deployment["componentIds"]) != site_legacy:
            raise VerificationError(f"{site}: replacement is not a site-local unified deployment")
        site_vcenter = one(
            [item for item in inventory["components"] if item["site"] == site and item["type"] == "vcenter"],
            f"{site} vCenter",
        )
        if deployment["order"] <= order_by_id[plan_by_id[site_vcenter["id"]]["changeStep"]]:
            raise VerificationError(f"{site}: unified recovery is deployed before vCenter 9.1")
        deployment_orders.append(deployment["order"])

    configure = one(
        [step for step in steps if step["action"] == "configure" and set(step["componentIds"]) == legacy_ids],
        "cross-site recovery pairing/configuration step",
    )
    if configure["order"] <= max(deployment_orders):
        raise VerificationError("site pairing is restored before both unified appliances are deployed")

    for component in plan["components"]:
        expected_action = "deploy" if component["id"] in legacy_ids else "upgrade"
        if steps_by_id[component["changeStep"]]["action"] != expected_action:
            raise VerificationError(f"{component['id']}: disposition and change action disagree")

    spec = plan["targetSddcSpec"]
    design = inventory["design"]
    if spec.get("sddcId") != design["sddcId"] or spec.get("workflowType") != "VCF":
        raise VerificationError("targetSddcSpec does not identify the intended brownfield VCF instance")
    if spec.get("version") != plan["targetVcfVersion"]:
        raise VerificationError("targetSddcSpec version is not the fleet target")
    if spec.get("vcfInstanceName") != design["vcfInstanceName"]:
        raise VerificationError("targetSddcSpec vcfInstanceName does not match inventory design")

    primary_vcenter = one(
        [item for item in inventory["components"] if item["site"] == design["managementSite"] and item["type"] == "vcenter"],
        "management-site vCenter",
    )
    vcenter_spec = spec["vcenterSpec"]
    if (
        vcenter_spec.get("vcenterHostname") != primary_vcenter["fqdn"]
        or vcenter_spec.get("version") != targets["vcenter"]["targetVersion"]
        or vcenter_spec.get("useExistingDeployment") is not True
    ):
        raise VerificationError("targetSddcSpec does not model the existing management-site vCenter")

    primary_nsx = one(
        [item for item in inventory["components"] if item["site"] == design["managementSite"] and item["type"] == "nsx"],
        "management-site NSX",
    )
    nsx_spec = spec.get("nsxtSpec", {})
    if (
        nsx_spec.get("vipFqdn") != primary_nsx["fqdn"]
        or nsx_spec.get("version") != targets["nsx"]["targetVersion"]
        or nsx_spec.get("useExistingDeployment") is not True
    ):
        raise VerificationError("targetSddcSpec does not model the existing management-site NSX")

    expected_hosts = {
        item["hostname"]
        for item in inventory["components"]
        if item["site"] == design["managementSite"] and item["type"] == "esxi"
    }
    actual_hosts = {item["hostname"] for item in spec.get("hostSpecs", [])}
    if actual_hosts != expected_hosts:
        raise VerificationError("targetSddcSpec hostSpecs do not cover the management-site ESXi hosts")
    expected_networks = {(item["networkType"], item["vlanId"]) for item in design["networks"]}
    actual_networks = {(item["networkType"], item["vlanId"]) for item in spec["networkSpecs"]}
    if actual_networks != expected_networks:
        raise VerificationError("targetSddcSpec networkSpecs do not match inventory design")
    if spec["dnsSpec"].get("subdomain") != design["dnsSubdomain"]:
        raise VerificationError("targetSddcSpec DNS subdomain does not match inventory design")
    if spec.get("ntpServers") != design["ntpServers"]:
        raise VerificationError("targetSddcSpec NTP servers do not match inventory design")

    if not core_ids or not legacy_ids:
        raise VerificationError("protected fixture no longer represents both core and recovery products")


def run_builder(inventory_path: Path, snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    command = [
        "pwsh",
        "-NoLogo",
        "-NoProfile",
        "-File",
        str(ROOT / "build-plan.ps1"),
        "-InventoryPath",
        str(inventory_path),
        "-CompatibilitySnapshotPath",
        str(snapshot_path),
        "-OutputPath",
        str(output_path),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr).strip()[-3000:]
        raise VerificationError(f"PowerShell module failed to generate the plan:\n{detail}")
    return load_json(output_path)


def regenerate_and_compare(plan: dict[str, Any]) -> None:
    if GENERATED_PATH.exists():
        GENERATED_PATH.unlink()
    try:
        regenerated = run_builder(INVENTORY_PATH, SNAPSHOT_PATH, GENERATED_PATH)
        if regenerated != plan:
            raise VerificationError("PowerShell module output differs from architecture/migration-plan.json")
    finally:
        if GENERATED_PATH.exists():
            GENERATED_PATH.unlink()


def validate_supplied_inventory_drives_output(
    openapi: dict[str, Any], plan_schema: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    inventory = load_json(INVENTORY_PATH)
    inventory["estateId"] = "northwind-dual-site-variant"
    design = inventory["design"]
    design["sddcId"] = "chi02-w02"
    design["vcfInstanceName"] = "northwind-variant"
    design["datacenterName"] = "chi02-dc"
    design["clusterName"] = "chi02-mgmt01"
    design["existingDatastoreName"] = "vsan-chi02-mgmt01"
    design["dnsSubdomain"] = "variant.example"
    design["dnsServers"] = ["198.51.100.53", "198.51.100.54"]
    design["ntpServers"] = ["198.51.100.123", "198.51.100.124"]
    for network in design["networks"]:
        network["vlanId"] += 100

    architecture_dir = ROOT / "architecture"
    with tempfile.TemporaryDirectory(prefix=".verify-variant-", dir=architecture_dir) as raw_temp:
        temp_dir = Path(raw_temp)
        variant_inventory_path = temp_dir / "estate.json"
        variant_output_path = temp_dir / "migration-plan.json"
        variant_inventory_path.write_text(
            json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
        )
        variant_plan = run_builder(variant_inventory_path, SNAPSHOT_PATH, variant_output_path)

    LocalSchemaValidator(openapi).validate(
        variant_plan.get("targetSddcSpec"),
        {"$ref": "#/components/schemas/SddcSpec"},
        "$.targetSddcSpec",
    )
    LocalSchemaValidator(plan_schema).validate(variant_plan, plan_schema)
    validate_semantics(variant_plan, inventory, snapshot)


def main() -> int:
    try:
        # Validate the executable architecture before its supporting research record.
        plan = load_json(PLAN_PATH)
        openapi = load_json(OPENAPI_PATH)
        target_sddc_spec = plan.get("targetSddcSpec") if isinstance(plan, dict) else None
        LocalSchemaValidator(openapi).validate(
            target_sddc_spec, {"$ref": "#/components/schemas/SddcSpec"}, "$.targetSddcSpec"
        )
        print("PASS installer SddcSpec schema")

        plan_schema = load_json(PLAN_SCHEMA_PATH)
        LocalSchemaValidator(plan_schema).validate(plan, plan_schema)
        print("PASS migration plan schema")

        inventory = load_json(INVENTORY_PATH)
        snapshot = load_json(SNAPSHOT_PATH)
        if snapshot["installerSpec"]["tag"] != "9.1.0.0":
            raise VerificationError("compatibility snapshot does not pin installer tag 9.1.0.0")
        actual_spec_hash = hashlib.sha256(OPENAPI_PATH.read_bytes()).hexdigest()
        if actual_spec_hash != snapshot["installerSpec"]["sha256"]:
            raise VerificationError("pinned installer specification hash does not match compatibility snapshot")
        validate_semantics(plan, inventory, snapshot)
        print("PASS pinned inventory and compatibility architecture")

        validate_research_record(inventory, snapshot)
        print("PASS live Broadcom research record")

        regenerate_and_compare(plan)
        validate_supplied_inventory_drives_output(openapi, plan_schema, snapshot)
        print("PASS deterministic VMware.Sdk.Vcf module output")
        return 0
    except (VerificationError, KeyError, TypeError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
