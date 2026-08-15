#!/usr/bin/env python3
"""Protected, offline verifier for the mixed-estate VCF architecture seed."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
ARTIFACT = ROOT / "artifacts" / "migration_plan.json"
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
PLAN_SCHEMA = ROOT / "schemas" / "migration-plan.schema.json"
INVENTORY = ROOT / "fixtures" / "estate_inventory.json"
SNAPSHOT = ROOT / "fixtures" / "compatibility_snapshot.json"
RESEARCH = ROOT / "research" / "consulted_sources.json"


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    relative = path.relative_to(ROOT)

    def reject_nonfinite(token: str) -> Any:
        raise VerificationError(f"{relative} contains non-finite JSON number {token}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise VerificationError(f"{relative} repeats JSON object key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except FileNotFoundError as exc:
        raise VerificationError(f"{relative} is missing") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"{relative} is not valid JSON: {exc.msg} at line {exc.lineno}"
        ) from exc


def json_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


class SchemaValidator:
    """Small Draft-07/OpenAPI schema validator using only the standard library."""

    def __init__(self, root_schema: dict[str, Any]):
        self.root_schema = root_schema

    def resolve_ref(self, ref: str) -> Any:
        if not ref.startswith("#/"):
            raise VerificationError(f"unsupported external schema reference {ref!r}")
        node: Any = self.root_schema
        for encoded in ref[2:].split("/"):
            token = encoded.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or token not in node:
                raise VerificationError(f"unresolvable schema reference {ref!r}")
            node = node[token]
        return node

    def validate(self, instance: Any, schema: Any, path: str = "$") -> None:
        if isinstance(schema, bool):
            if not schema:
                raise VerificationError(f"{path}: rejected by false schema")
            return
        if not isinstance(schema, dict):
            raise VerificationError(f"{path}: malformed schema node")

        if "$ref" in schema:
            self.validate(instance, self.resolve_ref(schema["$ref"]), path)
            return

        if instance is None and schema.get("nullable") is True:
            return

        for sub in schema.get("allOf", []):
            self.validate(instance, sub, path)

        if "anyOf" in schema:
            if not self._matches_any(instance, schema["anyOf"], path):
                raise VerificationError(f"{path}: does not satisfy anyOf")

        if "oneOf" in schema:
            matches = sum(self._matches(instance, sub, path) for sub in schema["oneOf"])
            if matches != 1:
                raise VerificationError(f"{path}: satisfies {matches} oneOf branches, expected 1")

        if "not" in schema and self._matches(instance, schema["not"], path):
            raise VerificationError(f"{path}: satisfies a forbidden schema")

        if "const" in schema and instance != schema["const"]:
            raise VerificationError(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and instance not in schema["enum"]:
            raise VerificationError(f"{path}: {instance!r} is not in {schema['enum']!r}")

        expected_type = schema.get("type")
        if expected_type is not None:
            types = [expected_type] if isinstance(expected_type, str) else expected_type
            if not any(json_type_matches(instance, candidate) for candidate in types):
                raise VerificationError(f"{path}: expected JSON type {expected_type!r}")

        if isinstance(instance, dict):
            self._validate_object(instance, schema, path)
        elif isinstance(instance, list):
            self._validate_array(instance, schema, path)
        elif isinstance(instance, str):
            self._validate_string(instance, schema, path)
        elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
            self._validate_number(instance, schema, path)

    def _matches(self, instance: Any, schema: Any, path: str) -> bool:
        try:
            self.validate(instance, schema, path)
        except VerificationError:
            return False
        return True

    def _matches_any(self, instance: Any, schemas: list[Any], path: str) -> bool:
        return any(self._matches(instance, sub, path) for sub in schemas)

    def _validate_object(self, instance: dict[str, Any], schema: dict[str, Any], path: str) -> None:
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            raise VerificationError(f"{path}: missing required properties {missing!r}")
        if len(instance) < schema.get("minProperties", 0):
            raise VerificationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise VerificationError(f"{path}: too many properties")

        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        for key, value in instance.items():
            matched = False
            if key in properties:
                self.validate(value, properties[key], f"{path}.{key}")
                matched = True
            for pattern, sub in pattern_properties.items():
                if re.search(pattern, key):
                    self.validate(value, sub, f"{path}.{key}")
                    matched = True
            if matched:
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                raise VerificationError(f"{path}: unexpected property {key!r}")
            if isinstance(additional, dict):
                self.validate(value, additional, f"{path}.{key}")

    def _validate_array(self, instance: list[Any], schema: dict[str, Any], path: str) -> None:
        if len(instance) < schema.get("minItems", 0):
            raise VerificationError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise VerificationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                raise VerificationError(f"{path}: items are not unique")
        items = schema.get("items")
        if isinstance(items, list):
            for index, (item, sub) in enumerate(zip(instance, items)):
                self.validate(item, sub, f"{path}[{index}]")
        elif items is not None:
            for index, item in enumerate(instance):
                self.validate(item, items, f"{path}[{index}]")

    def _validate_string(self, instance: str, schema: dict[str, Any], path: str) -> None:
        if len(instance) < schema.get("minLength", 0):
            raise VerificationError(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise VerificationError(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                raise VerificationError(f"{path}: invalid schema pattern: {exc}") from exc
            if matched is None:
                raise VerificationError(f"{path}: string does not match {schema['pattern']!r}")

    def _validate_number(self, instance: int | float, schema: dict[str, Any], path: str) -> None:
        if "minimum" in schema and instance < schema["minimum"]:
            raise VerificationError(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise VerificationError(f"{path}: number is above maximum")
        if isinstance(schema.get("exclusiveMinimum"), (int, float)) and not isinstance(
            schema.get("exclusiveMinimum"), bool
        ):
            if instance <= schema["exclusiveMinimum"]:
                raise VerificationError(f"{path}: number is not above exclusiveMinimum")
        if isinstance(schema.get("exclusiveMaximum"), (int, float)) and not isinstance(
            schema.get("exclusiveMaximum"), bool
        ):
            if instance >= schema["exclusiveMaximum"]:
                raise VerificationError(f"{path}: number is not below exclusiveMaximum")
        if "multipleOf" in schema:
            quotient = instance / schema["multipleOf"]
            if abs(quotient - round(quotient)) > 1e-9:
                raise VerificationError(f"{path}: number is not a multipleOf {schema['multipleOf']}")


def validate_installer_schema(plan: Any) -> None:
    """This is intentionally the first artifact acceptance check."""
    if not isinstance(plan, dict):
        raise VerificationError("artifact root must be an object containing target_sddc_spec")
    if "target_sddc_spec" not in plan:
        raise VerificationError("artifact is missing target_sddc_spec")
    openapi = load_json(OPENAPI)
    try:
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError) as exc:
        raise VerificationError("vendored installer document has no SddcSpec schema") from exc
    SchemaValidator(openapi).validate(plan["target_sddc_spec"], sddc_schema, "$.target_sddc_spec")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def require_unique(values: list[str], label: str) -> None:
    require(len(values) == len(set(values)), f"{label} must be unique")


def verify_research_record() -> None:
    record = load_json(RESEARCH)
    if isinstance(record, list):
        sources = record
    elif isinstance(record, dict):
        sources = record.get("consulted")
    else:
        sources = None
    require(isinstance(sources, list) and sources, "research record must contain at least one consulted source")

    urls: list[str] = []
    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        require(isinstance(source, dict), f"{label} must be an object")
        title = source.get("title")
        url = source.get("url")
        accessed = source.get("accessed", source.get("access_date"))
        fact = source.get("fact_used", source.get("fact"))
        require(isinstance(title, str) and title.strip(), f"{label} is missing a title")
        require(isinstance(url, str) and url.strip(), f"{label} is missing a URL")
        require(isinstance(accessed, str), f"{label} is missing an access date")
        require(isinstance(fact, str) and fact.strip(), f"{label} is missing the fact used")
        try:
            parsed_date = date.fromisoformat(accessed)
        except ValueError as exc:
            raise VerificationError(f"{label} access date must use YYYY-MM-DD") from exc
        require(parsed_date.isoformat() == accessed, f"{label} access date must use YYYY-MM-DD")

        parsed_url = urlsplit(url)
        hostname = (parsed_url.hostname or "").lower()
        reserved_host = (
            hostname in {"localhost", "example.com", "example.net", "example.org"}
            or hostname.endswith((".localhost", ".invalid", ".test"))
            or hostname.endswith((".example.com", ".example.net", ".example.org"))
        )
        require(
            parsed_url.scheme == "https"
            and parsed_url.username is None
            and parsed_url.password is None
            and "." in hostname
            and not reserved_host,
            f"{label} URL must be a reachable public HTTPS source",
        )
        urls.append(url)
    require_unique(urls, "research source URLs")


def verify_target_sddc_design(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    spec = plan["target_sddc_spec"]
    desired = inventory["desired_change"]
    design = inventory["target_design"]
    bom = snapshot["target_bom"]

    require(spec.get("sddcId") == desired["workload_domain_id"], "SddcSpec sddcId does not name the requested domain")
    require(spec.get("version") == bom["vcf_version"], "SddcSpec version does not match the target BOM")
    require(spec.get("dnsSpec", {}).get("subdomain") == design["dns_subdomain"], "SddcSpec DNS subdomain differs from inventory")
    require(spec.get("ntpServers") == design["ntp_servers"], "SddcSpec NTP servers differ from inventory")

    vcenter = spec.get("vcenterSpec", {})
    require(vcenter.get("vcenterHostname") == design["vcenter_hostname"], "SddcSpec vCenter hostname differs from inventory")
    require(vcenter.get("version") == bom["components"]["vcenter"], "SddcSpec vCenter version differs from target BOM")

    hosts = [host.get("hostname") for host in spec.get("hostSpecs", []) if isinstance(host, dict)]
    require(sorted(hosts) == sorted(design["hostnames"]), "SddcSpec hosts differ from requested target hosts")

    expected_networks = {
        item["network_type"]: (item["vlan_id"], item["subnet"], item["gateway"], item["mtu"])
        for item in design["networks"]
    }
    actual_networks: dict[str, tuple[Any, Any, Any, Any]] = {}
    for item in spec.get("networkSpecs", []):
        if not isinstance(item, dict) or "networkType" not in item:
            raise VerificationError("SddcSpec contains a malformed networkSpecs item")
        network_type = item["networkType"]
        require(network_type not in actual_networks, f"SddcSpec repeats network type {network_type}")
        actual_networks[network_type] = (
            item.get("vlanId"),
            item.get("subnet"),
            item.get("gateway"),
            item.get("mtu"),
        )
    require(actual_networks == expected_networks, "SddcSpec networks differ from requested target design")

    nsx = spec.get("nsxtSpec", {})
    require(nsx.get("vipFqdn") == design["nsx_vip_fqdn"], "SddcSpec NSX VIP differs from inventory")
    require(nsx.get("version") == bom["components"]["nsx"], "SddcSpec NSX version differs from target BOM")
    nsx_hosts = [node.get("hostname") for node in nsx.get("nsxtManagers", []) if isinstance(node, dict)]
    require(sorted(nsx_hosts) == sorted(design["nsx_manager_hostnames"]), "SddcSpec NSX managers differ from inventory")


def verify_component_table(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    inventory_rows = {row["id"]: row for row in inventory["components"]}
    plan_rows = {row["id"]: row for row in plan["components"]}
    require_unique([row["id"] for row in plan["components"]], "component ids")
    require(set(plan_rows) == set(inventory_rows), "component table must contain every and only inventory component")

    management_ids: set[str] = set()
    legacy_ids: set[str] = set()
    target_ids: set[str] = set()
    target_bom = snapshot["target_bom"]["components"]
    gate_ids = {gate["id"] for gate in plan["gates"]}

    for component_id, current in inventory_rows.items():
        proposed = plan_rows[component_id]
        require(proposed["current_version"] == current["current_version"], f"{component_id} current version was rewritten")
        require(set(proposed["gates"]).issubset(gate_ids), f"{component_id} references an unknown gate")
        if current["domain"] == "management":
            management_ids.add(component_id)
            require(proposed["disposition"] == "retain", f"{component_id} must be retained")
            require(proposed["target_version"] == current["current_version"], f"{component_id} must not change version")
        elif current["current_version"] == "absent":
            target_ids.add(component_id)
            require(proposed["disposition"] == "deploy", f"{component_id} must be deployed")
            require(proposed["target_version"] == target_bom[current["type"]], f"{component_id} target is outside the pinned BOM")
        else:
            legacy_ids.add(component_id)
            require(proposed["disposition"] == "retire", f"{component_id} must be retired after migration")
            require(proposed["target_version"] == "retired", f"{component_id} target must be retired")
    return management_ids, legacy_ids, target_ids


def verify_steps(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    management_ids: set[str],
    legacy_ids: set[str],
    target_ids: set[str],
) -> None:
    steps = sorted(plan["steps"], key=lambda step: step["order"])
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "step order must be contiguous from 1")
    require_unique([step["id"] for step in steps], "step ids")

    all_component_ids = {row["id"] for row in inventory["components"]}
    gates = plan["gates"]
    require_unique([gate["id"] for gate in gates], "gate ids")
    gate_ids = {gate["id"] for gate in gates}
    satisfied = {gate["id"] for gate in gates if gate["initial"]}
    for step in steps:
        require(set(step["component_ids"]).issubset(all_component_ids), f"step {step['id']} names an unknown component")
        require(not (set(step["component_ids"]) & management_ids), f"step {step['id']} disturbs the management domain")
        require(set(step["requires"]).issubset(gate_ids), f"step {step['id']} requires an unknown gate")
        require(set(step["satisfies"]).issubset(gate_ids), f"step {step['id']} satisfies an unknown gate")
        unavailable = set(step["requires"]) - satisfied
        require(not unavailable, f"step {step['id']} runs before gates are satisfied: {sorted(unavailable)}")
        repeated = set(step["satisfies"]) & satisfied
        require(not repeated, f"step {step['id']} re-satisfies gates: {sorted(repeated)}")
        satisfied.update(step["satisfies"])
    require(satisfied == gate_ids, "every non-initial gate must be satisfied by an ordered step")

    required_actions = snapshot["supported_route"]["required_action_order"]
    by_action: dict[str, list[dict[str, Any]]] = {}
    for action in required_actions:
        matches = [step for step in steps if step["action"] == action]
        require(matches, f"route is missing required {action!r} action")
        by_action[action] = matches
    for earlier, later in zip(required_actions, required_actions[1:]):
        require(
            max(step["order"] for step in by_action[earlier])
            < min(step["order"] for step in by_action[later]),
            "required route actions are out of order",
        )

    for action in ("deploy", "validate", "import"):
        covered = set().union(*(set(step["component_ids"]) for step in by_action[action]))
        require(covered == target_ids, f"{action} actions must cover exactly all target components")
    for action in ("quiesce", "export", "retire"):
        covered = set().union(*(set(step["component_ids"]) for step in by_action[action]))
        require(covered == legacy_ids, f"{action} actions must cover exactly all legacy components")
    cutover_components = set().union(*(set(step["component_ids"]) for step in by_action["cutover"]))
    require(cutover_components == legacy_ids | target_ids, "cutover must connect every source and target component")

    forbidden = {
        (row["component_type"], row["from"], row["to"])
        for row in snapshot["forbidden_direct_transitions"]
    }
    inventory_rows = {row["id"]: row for row in inventory["components"]}
    target_bom = snapshot["target_bom"]["components"]
    for step in steps:
        if step["action"] != "upgrade":
            continue
        for component_id in step["component_ids"]:
            row = inventory_rows[component_id]
            transition = (row["type"], row["current_version"], target_bom.get(row["type"]))
            require(transition not in forbidden, f"step {step['id']} attempts forbidden direct transition for {component_id}")


def verify_workloads(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    legacy_ids: set[str],
    target_ids: set[str],
) -> None:
    expected_ids = {row["id"] for row in inventory["workloads"]}
    migrations = {row["workload_id"]: row for row in plan["workload_migrations"]}
    require_unique([row["workload_id"] for row in plan["workload_migrations"]], "workload migration ids")
    require(set(migrations) == expected_ids, "plan must migrate every and only inventory workload")
    gate_ids = {gate["id"] for gate in plan["gates"]}
    expected_method = snapshot["supported_route"]["migration_method"]
    for workload_id, migration in migrations.items():
        require(migration["method"] == expected_method, f"{workload_id} uses an unsupported migration method")
        require(set(migration["source_component_ids"]) == legacy_ids, f"{workload_id} does not name all source components")
        require(set(migration["target_component_ids"]) == target_ids, f"{workload_id} does not name all target components")
        require(set(migration["gates"]).issubset(gate_ids), f"{workload_id} references an unknown gate")


def verify_plan_after_installer(plan: dict[str, Any]) -> None:
    plan_schema = load_json(PLAN_SCHEMA)
    SchemaValidator(plan_schema).validate(plan, plan_schema)
    inventory = load_json(INVENTORY)
    snapshot = load_json(SNAPSHOT)

    require(plan["estate_id"] == inventory["estate_id"], "estate_id differs from inventory")
    require(plan["compatibility_snapshot_id"] == snapshot["snapshot_id"], "wrong compatibility snapshot id")
    require(plan["objective"] == "add-workload-domain", "wrong architecture objective")
    require(
        inventory["desired_change"]["management_domain_mutation_allowed"] is False,
        "fixture no longer protects the management domain",
    )
    require(
        snapshot["target_bom"]["vcf_version"] == inventory["desired_change"]["target_vcf_version"],
        "fixture target and pinned BOM disagree",
    )

    verify_target_sddc_design(plan, inventory, snapshot)
    management_ids, legacy_ids, target_ids = verify_component_table(plan, inventory, snapshot)
    verify_steps(plan, inventory, snapshot, management_ids, legacy_ids, target_ids)
    verify_workloads(plan, inventory, snapshot, legacy_ids, target_ids)


def generate_with_package(output: Path) -> None:
    command = [
        sys.executable,
        "-S",
        "-m",
        "vcf_architecture",
        "--inventory",
        str(INVENTORY.relative_to(ROOT)),
        "--compatibility",
        str(SNAPSHOT.relative_to(ROOT)),
        "--output",
        str(output.relative_to(ROOT)),
    ]
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, timeout=20)
    if process.returncode != 0:
        detail = (process.stdout + "\n" + process.stderr).strip()
        raise VerificationError(f"package command failed: {detail[-1200:]}")
    if not output.is_file():
        raise VerificationError("package command did not create its requested output")


def main() -> int:
    # Apart from decoding the candidate JSON, installer-schema validation must
    # happen before the plan schema, fixtures, package, or architecture rules.
    try:
        plan = load_json(ARTIFACT)
        validate_installer_schema(plan)
    except VerificationError as exc:
        print(f"[FAIL] installer SddcSpec validation: {exc}")
        return 1
    print("[PASS] target_sddc_spec validates against installer SddcSpec")

    try:
        verify_research_record()
        verify_plan_after_installer(plan)
        with tempfile.TemporaryDirectory(prefix=".verify-", dir=ROOT) as temporary:
            regenerated_path = Path(temporary) / "migration_plan-1.json"
            repeated_path = Path(temporary) / "migration_plan-2.json"
            generate_with_package(regenerated_path)
            generate_with_package(repeated_path)
            regenerated = load_json(regenerated_path)
            validate_installer_schema(regenerated)
            verify_plan_after_installer(regenerated)
            require(plan == regenerated, "committed artifact differs from the package output")
            require(
                regenerated_path.read_bytes() == repeated_path.read_bytes(),
                "package output is not deterministic",
            )
        print("[PASS] migration plan matches the estate and pinned compatibility snapshot")
        print("[PASS] stdlib-only package regenerates a valid architecture")
        return 0
    except (VerificationError, subprocess.TimeoutExpired) as exc:
        print(f"[FAIL] architecture verification: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
