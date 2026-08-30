#!/usr/bin/env python3
"""Protected, offline verifier for the VCF Aria migration architecture seed."""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "spec" / "installer-spec.json"
ARTIFACT_PATH = ROOT / "architecture" / "migration-plan.json"
RESEARCH_PATH = ROOT / "research" / "consulted-sources.md"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def type_matches(value: Any, expected: str) -> bool:
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
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the Draft-07 subset used by the installed specification."""
    errors: list[str] = []

    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return [f"{path}: unsupported schema reference {ref!r}"]
        target: Any = root_schema
        for raw_part in ref[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                return [f"{path}: unresolved schema reference {ref}"]
            target = target[part]
        return validate_schema(value, target, root_schema, path)

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(value, item) for item in allowed):
            errors.append(f"{path}: expected type {allowed!r}, got {type(value).__name__}")
            return errors

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            for key in extras:
                errors.append(f"{path}: additional property {key!r} is not allowed")
        for key, child in properties.items():
            if key in value:
                errors.extend(validate_schema(value[key], child, root_schema, f"{path}.{key}"))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: expected at least {schema['minItems']} items, got {len(value)}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: expected at most {schema['maxItems']} items, got {len(value)}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, item_schema, root_schema, f"{path}[{index}]"))
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{path}: array items are not unique")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value {value} is below minimum {schema['minimum']}")

    return errors


def expected_placements(inventory: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    common = snapshot["placement_rules"]["common"]
    vcf = inventory["vcf_inventory"]
    inputs = inventory["design_inputs"]
    placement_base = {
        "version": snapshot["target_release"],
        "domain": vcf[common["domain_inventory_key"]],
        "cluster": vcf[common["cluster_inventory_key"]],
        "resource_pool": vcf[common["resource_pool_inventory_key"]],
        "network": vcf[common["network_inventory_key"]],
    }

    selected: list[tuple[str, dict[str, Any]]] = []
    for profile in snapshot["placement_rules"]["VCF Operations"]["profiles"]:
        if (
            inputs["operations"]["object_count"] <= profile["max_objects"]
            and inputs["operations"]["metric_count"] <= profile["max_metrics"]
        ):
            selected.append(("VCF Operations", profile))
            break

    for profile in snapshot["placement_rules"]["VCF Automation"]["profiles"]:
        if inputs["automation"]["maximum_concurrent_deployments"] <= profile["max_concurrent_deployments"]:
            selected.append(("VCF Automation", profile))
            break

    logs_rules = snapshot["placement_rules"]["VCF Operations for Logs"]
    required_storage = (
        inputs["logs"]["daily_ingest_gib"]
        * inputs["logs"]["retention_days"]
        * logs_rules["storage_headroom_factor"]
    )
    for profile in logs_rules["profiles"]:
        usable = (
            profile["node_count"]
            * profile["storage_gib_per_node"]
            * logs_rules["usable_storage_fraction"]
        )
        if usable >= required_storage:
            selected.append(("VCF Operations for Logs", profile))
            break

    result: list[dict[str, Any]] = []
    for component, profile in selected:
        row = {"component": component, **placement_base}
        for key in (
            "name",
            "node_count",
            "vcpu_per_node",
            "memory_gib_per_node",
            "storage_gib_per_node",
        ):
            target_key = "profile" if key == "name" else key
            row[target_key] = profile[key]
        result.append(row)
    return result


def semantic_errors(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> list[str]:
    errors: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    check(artifact["estate_id"] == inventory["estate_id"], "estate_id does not match inventory")
    check(
        artifact["compatibility_snapshot"] == snapshot["snapshot_id"],
        "compatibility_snapshot does not match installed snapshot",
    )
    check(artifact["design_basis_date"] == snapshot["as_of"], "design_basis_date does not match snapshot")
    check(artifact["target_release"] == snapshot["target_release"], "target_release does not match snapshot")

    expected_places = expected_placements(inventory, snapshot)
    actual_places = artifact["placements"]
    check(
        [row["component"] for row in actual_places] == [row["component"] for row in expected_places],
        "placements must be ordered VCF Operations, VCF Automation, VCF Operations for Logs",
    )
    for index, expected in enumerate(expected_places):
        if index >= len(actual_places):
            break
        actual = actual_places[index]
        for key, expected_value in expected.items():
            check(actual.get(key) == expected_value, f"placement {expected['component']} has incorrect {key}")

    vcf = inventory["vcf_inventory"]
    traffic = inventory["design_inputs"]["north_south"]
    edge_rules = snapshot["edge_rules"]
    selected_edge = next(
        profile
        for profile in edge_rules["profiles"]
        if profile["production_allowed"]
        and profile["max_validated_throughput_gbps"] >= traffic["failure_survivable_peak_gbps"]
    )
    edge = artifact["edge_design"]
    check(edge["form_factor"] == selected_edge["name"], "edge form factor does not cover failure throughput")
    check(edge["node_count"] == edge_rules["node_count"], "edge node count is incorrect")
    check(edge["ha_mode"] == edge_rules["ha_mode"], "edge HA mode is incorrect")
    check(edge["vcpu_per_node"] == selected_edge["vcpu_per_node"], "edge vCPU sizing is incorrect")
    check(edge["memory_gib_per_node"] == selected_edge["memory_gib_per_node"], "edge memory sizing is incorrect")
    expected_edge_placement = {
        output_key: vcf[inventory_key]
        for output_key, inventory_key in edge_rules["placement_keys"].items()
    }
    check(edge["placement"] == expected_edge_placement, "edge placement does not match VCF inventory")
    expected_throughput = {
        "sustained_gbps": traffic["sustained_gbps"],
        "peak_gbps": traffic["peak_gbps"],
        "failure_survivable_peak_gbps": traffic["failure_survivable_peak_gbps"],
        "profile_limit_gbps": selected_edge["max_validated_throughput_gbps"],
    }
    check(edge["throughput"] == expected_throughput, "edge throughput basis is incorrect")
    check(edge["host_pnics"] == vcf["edge_host_nics"], "host pNIC dual-fabric mapping is incorrect")

    expected_vnics: list[dict[str, Any]] = []
    for rule in edge_rules["edge_vnic_layout"]:
        if rule["role"] == "overlay":
            network = vcf[rule["network_inventory_key"]]
            expected_vnics.append(
                {
                    "vnic": rule["vnic"],
                    "role": rule["role"],
                    "network": network["name"],
                    "vlan": network["vlan"],
                    "fabric": rule["fabric"],
                    "peer_ip": None,
                }
            )
        else:
            uplink = vcf["northbound_uplinks"][rule["uplink_index"]]
            expected_vnics.append(
                {
                    "vnic": rule["vnic"],
                    "role": rule["role"],
                    "network": uplink["name"],
                    "vlan": uplink["vlan"],
                    "fabric": uplink["fabric"],
                    "peer_ip": uplink["peer_ip"],
                }
            )
    check(edge["edge_vnics"] == expected_vnics, "edge vNIC overlay/uplink layout is incorrect")
    expected_routing = {
        "local_asn": vcf["edge_local_asn"],
        "peers": [
            {
                "name": item["name"],
                "peer_ip": item["peer_ip"],
                "peer_asn": item["peer_asn"],
                "vlan": item["vlan"],
                "fabric": item["fabric"],
            }
            for item in vcf["northbound_uplinks"]
        ],
    }
    check(edge["routing"] == expected_routing, "BGP peer design is incorrect")
    speeds = [item["speed_gbps"] for item in vcf["edge_host_nics"]]
    check(edge["mtu"] == vcf["transport_mtu"], "edge MTU is incorrect")
    check(edge["aggregate_physical_capacity_gbps"] == sum(speeds), "aggregate uplink capacity is incorrect")
    check(edge["single_fabric_capacity_gbps"] == min(speeds), "single-fabric capacity is incorrect")
    check(
        edge["single_fabric_capacity_gbps"] >= traffic["failure_survivable_peak_gbps"],
        "single-fabric capacity does not survive the required peak",
    )

    source_by_id = {item["id"]: item for item in inventory["source_products"]}
    steps = artifact["migration_steps"]
    check([step["order"] for step in steps] == [1, 2, 3], "migration step order must be contiguous")
    check(
        [step["source"]["id"] for step in steps] == snapshot["migration_order"],
        "migration step source order does not match snapshot",
    )
    for step, source_id in zip(steps, snapshot["migration_order"]):
        source = source_by_id[source_id]
        product_rule = snapshot["products"][source_id]
        check(
            step["source"] == {"id": source_id, "product": source["product"], "version": source["version"]},
            f"{source_id}: source product or version is incorrect",
        )
        check(
            step["target"]
            == {"component": product_rule["target_component"], "version": product_rule["target_version"]},
            f"{source_id}: target component or version is incorrect",
        )
        check(step["migration_mode"] == product_rule["migration_mode"], f"{source_id}: migration mode is incorrect")
        check(step["placement_ref"] == product_rule["placement_component"], f"{source_id}: placement_ref is incorrect")

        expected_carry: list[dict[str, Any]] = []
        expected_abandon: list[dict[str, Any]] = []
        for content in source["content"]:
            rule = product_rule["content_rules"].get(content["kind"])
            if rule is None:
                errors.append(f"snapshot has no content rule for {source_id}/{content['kind']}")
                continue
            if rule["disposition"] == "carry":
                expected_carry.append(
                    {
                        "content_id": content["id"],
                        "method": rule["method"],
                        "target_state": rule["target_state"],
                    }
                )
            else:
                expected_abandon.append({"content_id": content["id"], "reason": rule["reason"]})
        check(step["carries_forward"] == expected_carry, f"{source_id}: carried content mapping is incomplete or incorrect")
        check(step["abandoned"] == expected_abandon, f"{source_id}: abandoned content mapping is incomplete or incorrect")

        gate_ids = [gate["gate_id"] for gate in step["gates"]]
        check(gate_ids == product_rule["required_gate_ids"], f"{source_id}: gates are missing, extra, or out of order")
        check(len(gate_ids) == len(set(gate_ids)), f"{source_id}: duplicate gate ids")

    boundaries = artifact["lifecycle_boundaries"]
    check(
        [item["source_id"] for item in boundaries] == snapshot["migration_order"],
        "lifecycle boundaries must follow source order",
    )
    for boundary, source_id in zip(boundaries, snapshot["migration_order"]):
        source = source_by_id[source_id]
        product_rule = snapshot["products"][source_id]
        expected = {
            "source_id": source_id,
            "product": source["product"],
            "version": source["version"],
            "end_of_general_support": product_rule["end_of_general_support"],
            "status_at_snapshot": product_rule["support_status_at_snapshot"],
        }
        for key, value in expected.items():
            check(boundary.get(key) == value, f"{source_id}: lifecycle {key} is incorrect")
        check(
            product_rule["end_of_general_support"] in boundary["plan_implication"],
            f"{source_id}: lifecycle plan_implication does not reflect the support boundary",
        )

    return errors


def research_errors() -> list[str]:
    """Validate the required, human-readable record of genuine source consultation."""
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ["research/consulted-sources.md is missing"]
    except OSError as exc:
        return [f"research/consulted-sources.md is unreadable: {type(exc).__name__}"]

    rows: list[tuple[str, str, str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4:
            continue
        if [cell.lower() for cell in cells] == [
            "title",
            "url",
            "access date",
            "decision informed",
        ]:
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        rows.append((cells[0], cells[1], cells[2], cells[3]))

    if not rows:
        return [
            "research/consulted-sources.md must contain the requested Markdown table "
            "with at least one source row"
        ]

    errors: list[str] = []
    urls: list[str] = []
    for index, (title, displayed_url, accessed, decision) in enumerate(rows, start=1):
        label = f"research row {index}"
        if len(title) < 5:
            errors.append(f"{label}: source title is missing or too short")
        url_match = re.fullmatch(r"<?(https://[^\s<>|]+)>?", displayed_url)
        if url_match is None:
            url_match = re.fullmatch(r"\[[^\]]+\]\((https://[^\s()|]+)\)", displayed_url)
        url = url_match.group(1) if url_match else displayed_url
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not parsed.path.strip("/")
            or not (hostname == "broadcom.com" or hostname.endswith(".broadcom.com"))
        ):
            errors.append(f"{label}: URL must be a real HTTPS Broadcom source")
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", accessed):
                raise ValueError
            date.fromisoformat(accessed)
        except ValueError:
            errors.append(f"{label}: access date must be a valid YYYY-MM-DD date")
        if len(decision) < 12:
            errors.append(f"{label}: decision informed is missing or too short")
        urls.append(url)

    if len(urls) != len(set(urls)):
        errors.append("research source URLs must not be duplicated")

    coverage = " ".join(f"{title} {decision}" for title, _, _, decision in rows).lower()
    topics = {
        "VMware Aria Operations transition": "operations",
        "VMware Aria Automation transition": "automation",
        "VMware Aria Operations for Logs transition": "logs",
        "content or configuration compatibility": r"content|configuration|integration|agent",
        "end-of-general-support boundary": r"support|eogs|end of general",
    }
    for topic, pattern in topics.items():
        if re.search(pattern, coverage) is None:
            errors.append(f"research record does not cover {topic}")
    edge_rows = [
        f"{title} {decision}".lower()
        for title, _, _, decision in rows
        if re.search(r"\b(?:nsx|edge)\b", f"{title} {decision}", re.IGNORECASE)
    ]
    if not any(
        re.search(r"throughput|uplink|form factor|sizing|capacity|mtu|bgp|limit", row)
        for row in edge_rows
    ):
        errors.append("research record does not cover NSX Edge sizing or uplink guidance")
    return errors


def ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def verify_module_regeneration(
    artifact_bytes: bytes,
    specification: dict[str, Any],
    inventory_document: dict[str, Any],
    snapshot_document: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    contract = specification["module_contract"]
    manifest = ROOT / contract["manifest_path"]
    inventory = ROOT / "estate" / "inventory.json"
    compatibility = ROOT / "spec" / "compatibility-snapshot.json"
    installer_spec = ROOT / "spec" / "installer-spec.json"

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as tmp:
        out_one = Path(tmp) / "plan-one.json"
        out_two = Path(tmp) / "plan-two.json"
        out_variant = Path(tmp) / "plan-variant.json"
        variant_inventory_path = Path(tmp) / "inventory-variant.json"
        variant_snapshot_path = Path(tmp) / "compatibility-variant.json"
        variant_specification_path = Path(tmp) / "installer-variant.json"

        variant_inventory = json.loads(json.dumps(inventory_document))
        variant_snapshot = json.loads(json.dumps(snapshot_document))
        variant_specification = json.loads(json.dumps(specification))

        variant_inventory["estate_id"] = "chi-private-cloud-variant"
        variant_vcf = variant_inventory["vcf_inventory"]
        variant_vcf.update(
            {
                "management_domain": "mgmt-domain-variant",
                "management_cluster": "cluster-mgmt-variant",
                "management_resource_pool": "rp-vcf-variant",
                "management_network": "dvpg-vcf-variant",
                "edge_domain": "edge-domain-variant",
                "edge_cluster": "cluster-edge-variant",
                "edge_resource_pool": "rp-edge-variant",
                "edge_management_network": "dvpg-edge-variant",
                "edge_local_asn": 65061,
                "transport_mtu": 8800,
            }
        )
        variant_vcf["edge_overlay_network"].update({"name": "edge-tep-variant", "vlan": 3710})
        for offset, uplink in enumerate(variant_vcf["northbound_uplinks"]):
            uplink.update(
                {
                    "name": f"variant-uplink-{offset + 1}",
                    "vlan": 3711 + offset,
                    "peer_ip": f"10.37.{11 + offset}.1",
                    "peer_asn": 65201 + offset,
                }
            )
        for nic in variant_vcf["edge_host_nics"]:
            nic["speed_gbps"] = 15

        variant_inputs = variant_inventory["design_inputs"]
        variant_inputs["operations"]["object_count"] = 50001
        variant_inputs["automation"]["maximum_concurrent_deployments"] = 51
        variant_inputs["logs"]["daily_ingest_gib"] = 100
        variant_inputs["north_south"].update(
            {"sustained_gbps": 7, "peak_gbps": 9, "failure_survivable_peak_gbps": 9}
        )
        variant_versions = ["8.18.6", "8.18.2", "8.18.4"]
        for source, version in zip(variant_inventory["source_products"], variant_versions):
            source["version"] = version
        variant_inventory["source_products"][1]["content"].append(
            {
                "id": "auto.templates.variant-catalog",
                "kind": "cloud_template_catalog",
                "name": "Variant catalog content",
            }
        )

        variant_snapshot.update(
            {
                "snapshot_id": "broadcom-vcf-aria-variant",
                "as_of": "2026-03-01",
                "target_release": "9.0.2",
            }
        )
        for product in variant_snapshot["products"].values():
            product["target_version"] = "9.0.2"
            product["end_of_general_support"] = "2028-01-31"
        variant_snapshot["migration_order"] = ["auto-01", "ops-01", "logs-01"]
        ops_gates = variant_snapshot["products"]["ops-01"]["required_gate_ids"]
        ops_gates[0], ops_gates[1] = ops_gates[1], ops_gates[0]
        dashboard_rule = variant_snapshot["products"]["ops-01"]["content_rules"]["dashboard"]
        dashboard_rule.update(
            {
                "method": "variant_preserve_in_place",
                "target_state": "Variant dashboard state derived from the supplied snapshot.",
            }
        )

        variant_specification["specification_version"] = "1.0.1"
        variant_specification["artifact_schema"]["properties"]["schema_version"]["const"] = "1.0.1"
        variant_inventory_path.write_text(
            json.dumps(variant_inventory, indent=2) + "\n", encoding="utf-8"
        )
        variant_snapshot_path.write_text(
            json.dumps(variant_snapshot, indent=2) + "\n", encoding="utf-8"
        )
        variant_specification_path.write_text(
            json.dumps(variant_specification, indent=2) + "\n", encoding="utf-8"
        )

        required_params = ",".join(contract["parameters"])
        command = f"""
$ErrorActionPreference = 'Stop'
Import-Module {ps_quote(manifest)} -Force
$moduleInfo = Get-Module {contract['module_name']} -ErrorAction Stop
$requiredModules = @($moduleInfo.RequiredModules)
if ($requiredModules.Count -ne 1 -or
    $requiredModules[0].Name -ne {ps_quote(Path(contract['required_sdk_module']))} -or
    $requiredModules[0].Version.ToString() -ne {ps_quote(Path(contract['required_sdk_version']))}) {{
    throw "Module manifest must require exactly {contract['required_sdk_module']} {contract['required_sdk_version']}"
}}
$exported = @(Get-Command -Module {contract['module_name']} -CommandType Function).Name | Sort-Object
if (($exported -join ',') -ne {ps_quote(Path(contract['exported_function']))}) {{
    throw "Module must export only {contract['exported_function']}; found $($exported -join ',')"
}}
$functionCommand = Get-Command {contract['exported_function']} -ErrorAction Stop
$requiredParameters = {ps_quote(Path(required_params))}.ToString().Split(',')
foreach ($parameterName in $requiredParameters) {{
    if (-not $functionCommand.Parameters.ContainsKey($parameterName)) {{
        throw "Missing required parameter $parameterName"
    }}
}}
{contract['exported_function']} -InventoryPath {ps_quote(inventory)} -CompatibilityPath {ps_quote(compatibility)} -InstallerSpecificationPath {ps_quote(installer_spec)} -OutputPath {ps_quote(out_one)}
{contract['exported_function']} -InventoryPath {ps_quote(inventory)} -CompatibilityPath {ps_quote(compatibility)} -InstallerSpecificationPath {ps_quote(installer_spec)} -OutputPath {ps_quote(out_two)}
{contract['exported_function']} -InventoryPath {ps_quote(variant_inventory_path)} -CompatibilityPath {ps_quote(variant_snapshot_path)} -InstallerSpecificationPath {ps_quote(variant_specification_path)} -OutputPath {ps_quote(out_variant)}
"""
        try:
            completed = subprocess.run(
                ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"PowerShell module verification could not run: {exc}"]
        if completed.returncode != 0:
            return [f"PowerShell module regeneration failed:\n{completed.stdout.strip()}"]
        if not out_one.is_file() or not out_two.is_file():
            return ["PowerShell module did not create both requested output files"]
        bytes_one = out_one.read_bytes()
        bytes_two = out_two.read_bytes()
        if bytes_one != bytes_two:
            errors.append("PowerShell module output is not byte-deterministic across identical runs")
        if bytes_one != artifact_bytes:
            errors.append("checked-in migration-plan.json is not the byte-exact output of the PowerShell module")
        if bytes_one.startswith(b"\xef\xbb\xbf"):
            errors.append("PowerShell module emitted a UTF-8 BOM")
        try:
            json.loads(bytes_one.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"PowerShell module output is not UTF-8 JSON: {exc}")
        if not out_variant.is_file():
            errors.append("PowerShell module did not create the requested variant output file")
        else:
            variant_bytes = out_variant.read_bytes()
            if variant_bytes.startswith(b"\xef\xbb\xbf"):
                errors.append("PowerShell module emitted a UTF-8 BOM for variant inputs")
            try:
                variant_artifact = json.loads(variant_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"PowerShell module variant output is not UTF-8 JSON: {exc}")
            else:
                variant_schema = variant_specification["artifact_schema"]
                variant_schema_errors = validate_schema(
                    variant_artifact, variant_schema, variant_schema
                )
                errors.extend(
                    f"variant schema: {message}" for message in variant_schema_errors
                )
                if not variant_schema_errors:
                    errors.extend(
                        f"variant semantics: {message}"
                        for message in semantic_errors(
                            variant_artifact, variant_inventory, variant_snapshot
                        )
                    )
    return errors


def main() -> int:
    # Contract requirement: load the installer's schema and validate the artifact
    # before loading fixtures, checking semantics, or importing candidate code.
    try:
        specification = load_json(SPEC_PATH)
        artifact_bytes = ARTIFACT_PATH.read_bytes()
        artifact = json.loads(artifact_bytes.decode("utf-8"))
    except FileNotFoundError:
        print("SCHEMA VALIDATION FAILED: required architecture/migration-plan.json is missing")
        return 1
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"SCHEMA VALIDATION FAILED: {exc}")
        return 1

    schema = specification.get("artifact_schema")
    if not isinstance(schema, dict):
        print("SCHEMA VALIDATION FAILED: installer specification has no artifact_schema")
        return 1
    schema_failures = validate_schema(artifact, schema, schema)
    if schema_failures:
        print("SCHEMA VALIDATION FAILED")
        for failure in schema_failures:
            print(f"- {failure}")
        return 1

    try:
        inventory = load_json(ROOT / "estate" / "inventory.json")
        snapshot = load_json(ROOT / "spec" / "compatibility-snapshot.json")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"PROTECTED FIXTURE ERROR: {exc}")
        return 1

    failures = semantic_errors(artifact, inventory, snapshot)
    failures.extend(research_errors())
    failures.extend(
        verify_module_regeneration(
            artifact_bytes, specification, inventory, snapshot
        )
    )
    if failures:
        print("VERIFICATION FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
