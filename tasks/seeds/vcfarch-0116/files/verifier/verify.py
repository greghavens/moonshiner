#!/usr/bin/env python3
"""Offline, deterministic verification for the VCF architecture artifact."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PINNED_HASHES = {
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "fixtures/estate-inventory.json": "01af33378fc540c668cbeeb3561c2f9a76b3d20dd3b56a335dbaadb3adfa3be0",
    "compatibility/vcf-9.1-pinned.json": "6e328c4df6d645b86f8a9de7ddf762befa4cbc82b88936894259bca8aaa12997",
    "schemas/migration-plan.schema.json": "9482af117dcdb6152cb512852365de0b0095333ccc8cc32ec1803e3e10b89f26",
}


class ValidationError(ValueError):
    pass


def fail(message: str) -> None:
    raise ValidationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot load {path.relative_to(ROOT)}: {exc}")


class SchemaValidator:
    """Small stdlib validator for the JSON Schema features used by the pins."""

    def __init__(self, document: dict[str, Any]):
        self.document = document

    def resolve(self, reference: str) -> dict[str, Any]:
        if not reference.startswith("#/"):
            fail(f"external schema reference is not supported: {reference}")
        current: Any = self.document
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            try:
                current = current[part]
            except (KeyError, TypeError):
                fail(f"unresolvable schema reference: {reference}")
        if not isinstance(current, dict):
            fail(f"schema reference does not resolve to an object: {reference}")
        return current

    def validate(self, value: Any, schema: dict[str, Any], path: str = "$") -> None:
        if "$ref" in schema:
            self.validate(value, self.resolve(schema["$ref"]), path)
            return

        if value is None and schema.get("nullable") is True:
            return

        if "const" in schema and value != schema["const"]:
            fail(f"{path}: must equal {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            fail(f"{path}: {value!r} is not in the allowed values")

        for subschema in schema.get("allOf", []):
            self.validate(value, subschema, path)

        if "anyOf" in schema:
            if not self._matches_any(value, schema["anyOf"], path):
                fail(f"{path}: does not satisfy anyOf")

        if "oneOf" in schema:
            matches = 0
            for subschema in schema["oneOf"]:
                try:
                    self.validate(value, subschema, path)
                    matches += 1
                except ValidationError:
                    pass
            if matches != 1:
                fail(f"{path}: must satisfy exactly one oneOf branch (matched {matches})")

        expected_type = schema.get("type")
        if expected_type is not None and not self._has_type(value, expected_type):
            fail(f"{path}: expected {expected_type}, got {type(value).__name__}")

        if isinstance(value, dict):
            self._validate_object(value, schema, path)
        elif isinstance(value, list):
            self._validate_array(value, schema, path)
        elif isinstance(value, str):
            self._validate_string(value, schema, path)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            self._validate_number(value, schema, path)

    def _matches_any(self, value: Any, schemas: list[Any], path: str) -> bool:
        for subschema in schemas:
            try:
                self.validate(value, subschema, path)
                return True
            except ValidationError:
                pass
        return False

    @staticmethod
    def _has_type(value: Any, expected: str | list[str]) -> bool:
        if isinstance(expected, list):
            return any(SchemaValidator._has_type(value, item) for item in expected)
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        return checks.get(expected, lambda _item: False)(value)

    def _validate_object(self, value: dict[str, Any], schema: dict[str, Any], path: str) -> None:
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            fail(f"{path}: missing required properties {missing}")

        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            child_path = f"{path}.{name}"
            if name in properties:
                self.validate(item, properties[name], child_path)
            elif additional is False:
                fail(f"{child_path}: additional property is not allowed")
            elif isinstance(additional, dict):
                self.validate(item, additional, child_path)

        if "minProperties" in schema and len(value) < schema["minProperties"]:
            fail(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            fail(f"{path}: too many properties")

    def _validate_array(self, value: list[Any], schema: dict[str, Any], path: str) -> None:
        if "minItems" in schema and len(value) < schema["minItems"]:
            fail(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            fail(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                self.validate(item, item_schema, f"{path}[{index}]")

    @staticmethod
    def _validate_string(value: str, schema: dict[str, Any], path: str) -> None:
        if "minLength" in schema and len(value) < schema["minLength"]:
            fail(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            fail(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                fail(f"{path}: invalid schema pattern: {exc}")
            if matched is None:
                fail(f"{path}: does not match {schema['pattern']!r}")

    @staticmethod
    def _validate_number(value: int | float, schema: dict[str, Any], path: str) -> None:
        if "minimum" in schema and value < schema["minimum"]:
            fail(f"{path}: less than minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            fail(f"{path}: greater than maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and not isinstance(schema["exclusiveMinimum"], bool):
            if value <= schema["exclusiveMinimum"]:
                fail(f"{path}: must be greater than {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and not isinstance(schema["exclusiveMaximum"], bool):
            if value >= schema["exclusiveMaximum"]:
                fail(f"{path}: must be less than {schema['exclusiveMaximum']}")


def assert_pins_unchanged() -> None:
    for relative, expected in PINNED_HASHES.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected input changed: {relative}")


def by_network_type(sddc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    networks = sddc.get("networkSpecs", [])
    result = {network.get("networkType"): network for network in networks}
    if len(result) != len(networks):
        fail("greenfield_sddc.networkSpecs contains duplicate network types")
    return result


def uplink_ids(dvs: dict[str, Any]) -> list[str]:
    return [mapping.get("id") for mapping in dvs.get("vmnicsToUplinks", [])]


def validate_greenfield_semantics(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    sddc = artifact["greenfield_sddc"]
    decision = artifact.get("storage_decision")
    if not isinstance(decision, dict):
        fail("storage_decision must be an object")

    selected = snapshot["selected_storage"]
    options = snapshot["storage_options"]
    if decision.get("selected") != selected:
        fail("storage_decision.selected does not match the pinned selection")
    expected_alternatives = {
        name: {
            "capacity_sized_hosts": details["capacity_sized_hosts"],
            "data_network_gbps": details["required_data_uplink_gbps"],
            "data_uplinks": details["required_data_uplinks"],
        }
        for name, details in options.items()
    }
    if decision.get("alternatives") != expected_alternatives:
        fail("storage_decision.alternatives does not expose the pinned OSA/ESA tradeoff")
    if decision.get("selected_hosts") != snapshot["selected_hosts"]:
        fail("storage_decision.selected_hosts is not the pinned host set")
    if decision.get("data_uplinks") != snapshot["data_uplinks"]:
        fail("storage_decision.data_uplinks is not the selected data fabric")

    greenfield = inventory["greenfield"]
    if sddc.get("sddcId") != greenfield["sddc_id"]:
        fail("greenfield_sddc.sddcId does not match inventory")
    if sddc.get("vcfInstanceName") != greenfield["vcf_instance_name"]:
        fail("greenfield_sddc.vcfInstanceName does not match inventory")
    if sddc.get("workflowType") != "VCF" or sddc.get("version") != snapshot["target_vcf_version"]:
        fail("greenfield SDDC must be a new VCF deployment at the pinned version")

    actual_hosts = [host.get("hostname") for host in sddc.get("hostSpecs", [])]
    if actual_hosts != snapshot["selected_hosts"]:
        fail("greenfield_sddc.hostSpecs must contain the selected hosts in pinned order")
    selected_option = options[selected]
    if len(actual_hosts) != selected_option["capacity_sized_hosts"]:
        fail("greenfield host count does not match capacity sizing")

    candidates = {host["hostname"]: host for host in greenfield["candidate_hosts"]}
    for hostname in actual_hosts:
        host = candidates.get(hostname)
        if host is None:
            fail(f"selected host is absent from inventory: {hostname}")
        if selected_option.get("requires_esa_ready_node") and not host.get("esa_ready_node"):
            fail(f"selected host is not ESA ReadyNode eligible: {hostname}")
        if not set(host["storage"]).issubset(set(selected_option["allowed_media"])):
            fail(f"selected host has unsupported {selected} media: {hostname}")
        for nic in snapshot["data_uplinks"]:
            if host["nics_gbps"].get(nic, 0) < selected_option["required_data_uplink_gbps"]:
                fail(f"{hostname}.{nic} is too slow for the selected storage design")

    vsan = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    if vsan.get("esaConfig", {}).get("enabled") is not True:
        fail("greenfield datastore must enable the selected ESA architecture")

    distributed_switches = sddc.get("dvsSpecs", [])
    if len(distributed_switches) != 2:
        fail("greenfield design must contain exactly the two dedicated DVSes")
    management = [dvs for dvs in distributed_switches if dvs.get("networks") == ["MANAGEMENT"]]
    data = [
        dvs
        for dvs in distributed_switches
        if len(dvs.get("networks", [])) == 2
        and set(dvs["networks"]) == {"VSAN", "VMOTION"}
    ]
    if len(management) != 1 or uplink_ids(management[0]) != snapshot["management_uplinks"]:
        fail("greenfield design needs one dedicated management DVS on the pinned uplinks")
    if len(data) != 1 or uplink_ids(data[0]) != snapshot["data_uplinks"]:
        fail("greenfield design needs one dedicated vSAN/vMotion data DVS on the pinned uplinks")
    if data[0].get("mtu", 0) < greenfield["networks"]["VSAN"]["mtu"]:
        fail("data DVS MTU is smaller than the vSAN network MTU")
    zones = data[0].get("nsxtSwitchConfig", {}).get("transportZones", [])
    if not any(zone.get("transportType") == "OVERLAY" for zone in zones):
        fail("data DVS must carry the NSX overlay transport zone")

    actual_networks = by_network_type(sddc)
    if set(actual_networks) != set(greenfield["networks"]):
        fail("greenfield network types do not match inventory")
    for network_type, expected in greenfield["networks"].items():
        actual = actual_networks[network_type]
        fields = {
            "vlanId": expected["vlan_id"],
            "subnet": expected["subnet"],
            "gateway": expected["gateway"],
            "mtu": expected["mtu"],
            "includeIpAddressRanges": [
                {"startIpAddress": expected["range"][0], "endIpAddress": expected["range"][1]}
            ],
        }
        for key, expected_value in fields.items():
            if actual.get(key) != expected_value:
                fail(f"network {network_type}.{key} does not match inventory")

    if sddc.get("dnsSpec") != {
        "subdomain": greenfield["subdomain"],
        "nameservers": greenfield["dns_servers"],
    }:
        fail("greenfield DNS configuration does not match inventory")
    if sddc.get("ntpServers") != greenfield["ntp_servers"]:
        fail("greenfield NTP configuration does not match inventory")
    if sddc.get("vcenterSpec", {}).get("useExistingDeployment") is not False:
        fail("greenfield vCenter must be a new deployment")
    nsx = sddc.get("nsxtSpec", {})
    if nsx.get("useExistingDeployment") is not False or len(nsx.get("nsxtManagers", [])) != 3:
        fail("greenfield NSX must be a new three-manager deployment")
    if nsx.get("transportVlanId") != greenfield["nsx_transport_vlan_id"]:
        fail("greenfield NSX transport VLAN does not match inventory")


def validate_migration(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    plan = artifact.get("migration_plan")
    if not isinstance(plan, dict):
        fail("migration_plan must be an object")
    plan_schema = load_json(ROOT / "schemas/migration-plan.schema.json")
    SchemaValidator(plan_schema).validate(plan, plan_schema)

    if plan["estate_id"] != inventory["estate_id"]:
        fail("migration estate_id does not match inventory")
    if plan["target_fleet"] != snapshot["target_fleet"]:
        fail("migration target_fleet does not match snapshot")

    inventory_components = {item["id"]: item for item in inventory["components"]}
    steps = plan["steps"]
    if [step["order"] for step in steps] != list(range(1, len(steps) + 1)):
        fail("migration orders must be contiguous and array-ordered from 1")
    component_ids = [step["component_id"] for step in steps]
    if len(component_ids) != len(set(component_ids)):
        fail("migration contains duplicate component steps")
    if set(component_ids) != set(inventory_components):
        fail("migration must name every inventory component exactly once")

    positions = {component_id: index for index, component_id in enumerate(component_ids)}
    targets = snapshot["component_targets"]
    for step in steps:
        component_id = step["component_id"]
        source = inventory_components[component_id]
        target = targets[component_id]
        if step["component"] != source["name"] or step["from_version"] != source["current_version"]:
            fail(f"migration source identity/version mismatch for {component_id}")
        if step["target"] != {
            "component": target["target_name"],
            "version": target["target_version"],
        }:
            fail(f"migration target mismatch for {component_id}")
        if step["action"] != target["action"]:
            fail(f"migration action mismatch for {component_id}")
        gate_ids = [gate["id"] for gate in step["gates"]]
        if gate_ids != target["required_gates"]:
            fail(f"migration gates mismatch for {component_id}")

    for before, after in snapshot["ordering"]:
        if positions[before] >= positions[after]:
            fail(f"migration ordering violated: {before} must precede {after}")


def validate_research(artifact: dict[str, Any]) -> None:
    records = artifact.get("research_consulted")
    if not isinstance(records, list) or not records:
        fail("research_consulted must be a non-empty array")

    searchable: list[str] = []
    for index, record in enumerate(records):
        path = f"research_consulted[{index}]"
        if not isinstance(record, dict):
            fail(f"{path} must be an object")
        for field in ("title", "publisher", "url", "accessed_at", "informed"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                fail(f"{path}.{field} must be a non-empty string")

        parsed = urlsplit(record["url"])
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            fail(f"{path}.url must be a Broadcom HTTP(S) source")
        try:
            date.fromisoformat(record["accessed_at"])
        except ValueError:
            fail(f"{path}.accessed_at must be an ISO-8601 calendar date")
        if "broadcom" not in record["publisher"].lower():
            fail(f"{path}.publisher must identify Broadcom")
        searchable.append(
            " ".join((record["title"], hostname, record["informed"])).lower()
        )

    categories = {
        "compatibility": ("compatibility", "compatible"),
        "interoperability": ("interoperability", "interopmatrix"),
        "hardware compatibility": ("hardware", "firmware", "readynode"),
        "upgrade path": ("upgrade", "update sequence", "transition"),
    }
    for category, markers in categories.items():
        if not any(any(marker in text for marker in markers) for text in searchable):
            fail(f"research_consulted does not identify a {category} source/decision")


def validate_portability_and_synthetic_secrets(artifact: dict[str, Any]) -> None:
    def walk(value: Any, key: str = "$") -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                walk(child, name)
        elif isinstance(value, list):
            for child in value:
                walk(child, key)
        elif isinstance(value, str):
            if re.match(r"^(?:/|[A-Za-z]:[\\/])", value):
                fail(f"artifact contains a host-specific absolute path in {key}")
            if "password" in key.lower() and not re.search(
                r"(?:synthetic|example|greenfield|lab)", value, re.IGNORECASE
            ):
                fail(f"{key} is not clearly marked as a synthetic lab credential")

    walk(artifact)


def validate_stdlib_package() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if project.get("project", {}).get("dependencies") not in (None, []):
        fail("project dependencies must remain empty")
    stdlib = set(sys.stdlib_module_names) | {"vcf_architect"}
    for path in (ROOT / "vcf_architect").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            fail(f"cannot parse package module {path.name}: {exc}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                roots = []
            for module in roots:
                if module not in stdlib:
                    fail(f"non-stdlib import in {path.name}: {module}")


def validate_regeneration(original: dict[str, Any], original_bytes: bytes) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "vcf_architect"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        fail(f"package entry point failed: {completed.stderr.strip()}")
    regenerated = load_json(ROOT / "architecture.json")
    if regenerated != original:
        fail("package did not deterministically regenerate the complete artifact")
    regenerated_bytes = (ROOT / "architecture.json").read_bytes()
    if regenerated_bytes != original_bytes:
        fail("package regeneration changed architecture.json bytes")


def verify() -> None:
    artifact_path = ROOT / "architecture.json"
    artifact = load_json(artifact_path)
    artifact_bytes = artifact_path.read_bytes()
    installer_spec = load_json(ROOT / "specifications/vcf-installer/vcf-installer-openapi.json")

    # This is deliberately the first validation performed by the verifier.
    try:
        greenfield_sddc = artifact["greenfield_sddc"]
    except (KeyError, TypeError):
        fail("$: missing greenfield_sddc required for installer SddcSpec validation")
    sddc_schema = installer_spec["components"]["schemas"]["SddcSpec"]
    SchemaValidator(installer_spec).validate(greenfield_sddc, sddc_schema, "$.greenfield_sddc")

    assert_pins_unchanged()
    inventory = load_json(ROOT / "fixtures/estate-inventory.json")
    snapshot = load_json(ROOT / "compatibility/vcf-9.1-pinned.json")
    validate_greenfield_semantics(artifact, inventory, snapshot)
    validate_migration(artifact, inventory, snapshot)
    validate_research(artifact)
    validate_portability_and_synthetic_secrets(artifact)
    validate_stdlib_package()
    validate_regeneration(artifact, artifact_bytes)


if __name__ == "__main__":
    try:
        verify()
    except (ValidationError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: architecture and package satisfy the pinned VCF design contract")
