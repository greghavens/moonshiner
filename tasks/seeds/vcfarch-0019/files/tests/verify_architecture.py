#!/usr/bin/env python3
"""Protected, offline acceptance checks for the VCF architecture artifacts."""

from __future__ import annotations

import ast
import copy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent


class VerificationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def read_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number {value!r}")
            ),
        )
    except FileNotFoundError:
        fail(f"missing required artifact: {path.relative_to(ROOT)}")
    except (json.JSONDecodeError, ValueError) as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        fail(f"unsupported non-local schema reference: {pointer}")
    node = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[part]
        except (KeyError, TypeError):
            fail(f"broken schema reference: {pointer}")
    return node


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
    fail(f"unsupported JSON Schema type: {expected}")


def validate_json_schema(
    value: Any,
    schema: Any,
    document: Any,
    path: str = "$",
) -> list[str]:
    """Validate the JSON Schema/OpenAPI subset used by the protected contracts."""
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: rejected by false schema"]
    if not isinstance(schema, dict):
        return [f"{path}: invalid schema node"]
    if "$ref" in schema:
        return validate_json_schema(value, json_pointer(document, schema["$ref"]), document, path)

    errors: list[str] = []
    for child in schema.get("allOf", []):
        errors.extend(validate_json_schema(value, child, document, path))
    if "anyOf" in schema:
        matches = [
            validate_json_schema(value, child, document, path)
            for child in schema["anyOf"]
        ]
        if not any(not match for match in matches):
            errors.append(f"{path}: does not match anyOf")
    if "oneOf" in schema:
        matches = sum(
            not validate_json_schema(value, child, document, path)
            for child in schema["oneOf"]
        )
        if matches != 1:
            errors.append(f"{path}: matches {matches} oneOf branches, expected 1")
    if "not" in schema and not validate_json_schema(value, schema["not"], document, path):
        errors.append(f"{path}: matches prohibited schema")

    if value is None and schema.get("nullable") is True:
        return errors
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not in enum {schema['enum']!r}")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(type_matches(value, item) for item in expected_type):
            errors.append(f"{path}: expected one of types {expected_type!r}")
            return errors
    elif isinstance(expected_type, str) and not type_matches(value, expected_type):
        errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
        return errors

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, child_value in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(
                    validate_json_schema(child_value, properties[key], document, child_path)
                )
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    validate_json_schema(
                        child_value, schema["additionalProperties"], document, child_path
                    )
                )
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{path}: too many properties")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True) for item in value]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: array items are not unique")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(
                    validate_json_schema(item, schema["items"], document, f"{path}[{index}]")
                )

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(f"{path}: string does not match {schema['pattern']!r}")
            except re.error as exc:
                fail(f"invalid protected schema regex at {path}: {exc}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: value is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: value is not below exclusiveMaximum")
    return errors


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def assert_mapping(actual: Any, expected: dict[str, Any], label: str) -> None:
    expect(isinstance(actual, dict), f"{label} must be an object")
    for key, value in expected.items():
        expect(actual.get(key) == value, f"{label}.{key} must be {value!r}")


def demand_fits(demand: dict[str, int], capacity: dict[str, int], label: str) -> None:
    expect(set(demand) <= set(capacity), f"{label} capacity omits a demand dimension")
    for dimension, amount in demand.items():
        expect(capacity[dimension] >= amount, f"{label} is undersized for {dimension}")


def validate_research_record(record: Any) -> None:
    expect(isinstance(record, dict), "research-sources.json must contain an object")
    researched_at = record.get("researched_at")
    expect(isinstance(researched_at, str) and researched_at, "researched_at must be a timestamp")
    try:
        datetime.fromisoformat(researched_at.replace("Z", "+00:00"))
    except ValueError:
        fail("researched_at must be an ISO 8601 timestamp")

    sources = record.get("sources")
    expect(isinstance(sources, list) and sources, "research sources must be a nonempty array")
    for index, source in enumerate(sources):
        label = f"research source {index}"
        expect(isinstance(source, dict), f"{label} must be an object")
        expect(isinstance(source.get("title"), str) and source["title"].strip(), f"{label} needs a title")
        url = source.get("url")
        expect(isinstance(url, str), f"{label} needs a URL")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        broadcom_published = any(
            host == domain or host.endswith(f".{domain}")
            for domain in ("broadcom.com", "vmware.com")
        )
        expect(parsed.scheme == "https" and broadcom_published, f"{label} must use a Broadcom-published HTTPS URL")
        claims = source.get("claims")
        expect(
            isinstance(claims, list)
            and claims
            and all(isinstance(claim, str) and claim.strip() for claim in claims),
            f"{label} needs nonempty compatibility or upgrade claims",
        )


def validate_sddc_semantics(
    sddc: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    installer = snapshot["installer"]
    expect(sddc.get("workflowType") == installer["workflow_type"], "workflowType must be VCF")
    expect(sddc.get("version") == installer["target_version"], "SddcSpec version mismatch")
    expect(sddc.get("vcfInstanceName") == requirements["vcf_instance_name"], "VCF instance name mismatch")

    management = next(d for d in requirements["domains"] if d["domain_type"] == "management")
    expected_hosts = management["hostnames"]
    actual_hosts = [host.get("hostname") for host in sddc.get("hostSpecs", [])]
    expect(actual_hosts == expected_hosts, "management hostSpecs do not match the six-host design")
    expect(len(set(actual_hosts)) == installer["required_management_hosts"], "management hosts are not unique")

    network_key = {
        "MANAGEMENT": "management",
        "VMOTION": "vmotion",
        "VSAN": "vsan",
        "VM_MANAGEMENT": "vm_management",
        "FLEET_MANAGEMENT": "fleet_management",
    }
    network_specs = sddc.get("networkSpecs", [])
    networks = {item.get("networkType"): item for item in network_specs}
    expect(set(network_key) <= set(networks), "SddcSpec omits a required network type")
    for network_type, fixture_key in network_key.items():
        expect(
            sum(item.get("networkType") == network_type for item in network_specs) == 1,
            f"SddcSpec must contain exactly one {network_type} network",
        )
        fixture = requirements["networking"][fixture_key]
        actual = networks[network_type]
        assert_mapping(
            actual,
            {"vlanId": fixture["vlan_id"], "subnet": fixture["cidr"], "gateway": fixture["gateway"]},
            f"network {network_type}",
        )

    assert_mapping(
        sddc.get("dnsSpec"),
        {
            "subdomain": requirements["networking"]["dns_domain"],
            "nameservers": requirements["networking"]["dns_servers"],
        },
        "dnsSpec",
    )
    expect(sddc.get("ntpServers") == requirements["networking"]["ntp_servers"], "NTP servers mismatch")

    target = requirements["target_version"]
    expect(sddc.get("sddcManagerSpec", {}).get("version") == target, "SDDC Manager target mismatch")
    expect(sddc.get("vcenterSpec", {}).get("version") == target, "vCenter target mismatch")
    expect(sddc.get("nsxtSpec", {}).get("version") == target, "VCF Networking target mismatch")

    operations = sddc.get("vcfOperationsSpec", {})
    ops_profile = snapshot["service_profiles"]["VCF Operations"]
    expect(operations.get("version") == target, "VCF Operations target mismatch")
    expect(operations.get("applianceSize") == ops_profile["profile"], "VCF Operations size mismatch")
    expect(len(operations.get("nodes", [])) == ops_profile["instances"], "VCF Operations node count mismatch")
    expect(
        [node.get("type") for node in operations["nodes"]] == ["master", "replica", "data"],
        "VCF Operations nodes must identify master, replica, and data roles",
    )

    collector = sddc.get("vcfOperationsCollectorSpec", {})
    collector_profile = snapshot["service_profiles"]["VCF Operations Collector"]
    expect(collector.get("version") == target, "VCF Operations collector target mismatch")
    expect(collector.get("applianceSize") == collector_profile["profile"], "collector size mismatch")

    automation = sddc.get("vcfAutomationSpec", {})
    automation_profile = snapshot["service_profiles"]["VCF Automation"]
    expect(automation.get("version") == target, "VCF Automation target mismatch")
    expect(automation.get("size") == automation_profile["profile"], "VCF Automation size mismatch")
    expect(
        automation.get("internalClusterCidr") == requirements["networking"]["automation_internal_cidr"],
        "Automation internal CIDR mismatch",
    )

    vsp = sddc.get("vspClusterSpec", {})
    expect(vsp.get("version") == target, "VCF Management Services target mismatch")
    expect(vsp.get("size") == "large", "VCF Management Services must use the large profile")
    expect(vsp.get("internalClusterCidrIpv4") == requirements["networking"]["vsp_internal_cidr"], "VSP internal CIDR mismatch")
    expect(
        vsp.get("ipv4Pool", {}).get("addresses") == requirements["networking"]["vsp_pool"]["addresses"],
        "VSP address pool must contain the twelve reserved addresses",
    )


def validate_architecture(
    architecture: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    assert_mapping(
        architecture,
        {
            "schema_version": "1.0",
            "design_id": requirements["design_id"],
            "target_version": requirements["target_version"],
        },
        "architecture",
    )
    sites = architecture.get("sites")
    expect(isinstance(sites, list), "sites must be a list")
    expect(len(sites) == len(requirements["sites"]), "site architecture is missing or duplicated")
    by_site = {site.get("site_id"): site for site in sites if isinstance(site, dict)}
    expect(len(by_site) == len(sites), "site architecture is missing or duplicated")
    for site in requirements["sites"]:
        assert_mapping(
            by_site.get(site["site_id"]),
            {"role": site["role"], "location": site["location"]},
            f"site {site['site_id']}",
        )

    availability = requirements["availability"]
    capacities = architecture.get("domain_capacity")
    expect(isinstance(capacities, list), "domain_capacity must be a list")
    expect(len(capacities) == len(requirements["domains"]), "domain capacity entries are missing or duplicated")
    by_domain = {item.get("domain_name"): item for item in capacities if isinstance(item, dict)}
    expect(len(by_domain) == len(requirements["domains"]), "domain capacity entries are missing or duplicated")
    primary_workload = next(
        domain for domain in requirements["domains"] if domain["domain_name"] == "ord-prod"
    )
    recovery = next(domain for domain in requirements["domains"] if domain["domain_name"] == "dfw-dr")
    fraction = availability["recovery_site_capacity_fraction"]
    for dimension, primary_amount in primary_workload["required_capacity"].items():
        expect(
            recovery["required_capacity"][dimension] == math.ceil(primary_amount * fraction),
            f"recovery demand does not satisfy the {fraction:.0%} site requirement for {dimension}",
        )

    for domain in requirements["domains"]:
        actual = by_domain[domain["domain_name"]]
        reserve = 1
        remaining = domain["host_count"] - reserve
        provided = {
            dimension: domain["host_profile"][profile_key] * remaining
            for dimension, profile_key in {
                "physical_cores": "physical_cores",
                "memory_gib": "memory_gib",
                "usable_storage_tib": "usable_storage_tib",
            }.items()
        }
        headroom = availability["workload_headroom_percent"] if domain["domain_type"] == "workload" else 0
        required = {
            dimension: math.ceil(amount * (100 + headroom) / 100)
            for dimension, amount in domain["required_capacity"].items()
        }
        assert_mapping(
            actual,
            {
                "domain_type": domain["domain_type"],
                "site_id": domain["site_id"],
                "cluster_name": domain["cluster_name"],
                "host_count": domain["host_count"],
                "reserve_hosts": reserve,
                "available_hosts_after_reserve": remaining,
                "provided_after_reserve": provided,
                "required_with_headroom": required,
                "meets_requirement": True,
            },
            f"capacity {domain['domain_name']}",
        )
        for dimension in provided:
            expect(provided[dimension] >= required[dimension], f"{domain['domain_name']} is undersized for {dimension}")

    demand_by_component = {
        "VCF Operations": requirements["service_demand"]["vcf_operations"] | {"remote_sites": 0},
        "VCF Operations Collector": {"remote_sites": requirements["service_demand"]["vcf_operations"]["remote_sites"]},
        "VCF Automation": requirements["service_demand"]["vcf_automation"],
        "VCF Operations for Logs": requirements["service_demand"]["vcf_operations_for_logs"],
    }
    demand_by_component["VCF Operations"].pop("remote_sites")
    services = architecture.get("service_placements")
    expect(isinstance(services, list), "service_placements must be a list")
    by_component = {item.get("component"): item for item in services if isinstance(item, dict)}
    required_components = set(snapshot["service_profiles"])
    expect(len(services) == len(required_components), "service placements are missing or duplicated")
    expect(set(by_component) == required_components, "service placements are missing or duplicated")
    for component, profile in snapshot["service_profiles"].items():
        actual = by_component[component]
        expected = {
            "version": requirements["target_version"],
            "profile": profile["profile"],
            "deployment_model": profile["deployment_model"],
            "instances": profile["instances"],
            "site_id": profile["site_id"],
            "domain_name": profile["domain_name"],
            "cluster_name": profile["cluster_name"],
            "demand": demand_by_component[component],
            "capacity": profile["capacity"],
            "meets_demand": True,
        }
        if "target_component" in profile:
            expected["target_component"] = profile["target_component"]
        assert_mapping(actual, expected, f"service {component}")
        demand_fits(actual["demand"], actual["capacity"], component)

    strategy = architecture.get("availability_design", {})
    assert_mapping(
        strategy,
        {
            "management_host_failures_to_tolerate": 1,
            "service_node_placement": "anti-affinity-across-distinct-hosts",
            "primary_site": "ORD01",
            "recovery_site": "DFW01",
            "recovery_site_capacity_fraction": fraction,
        },
        "availability_design",
    )


def expected_transition_ids(
    inventory: dict[str, Any], snapshot: dict[str, Any]
) -> set[str]:
    transitions = snapshot["allowed_transitions"]
    by_source: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for transition in transitions:
        by_source.setdefault((transition["component"], transition["from_version"]), []).append(transition)
    expected: set[str] = set()
    for item in inventory["components"]:
        state = (item["component"], item["version"])
        target = (item["target_component"], item["target_version"])
        seen: set[tuple[str, str]] = set()
        while state != target:
            expect(state not in seen, f"compatibility snapshot has a cycle from {state}")
            seen.add(state)
            choices = by_source.get(state, [])
            expect(len(choices) == 1, f"compatibility snapshot has no unique transition from {state}")
            transition = choices[0]
            expected.add(transition["transition_id"])
            state = (transition["target_component"], transition["target_version"])
    return expected


def validate_migration(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    assert_mapping(
        plan,
        {
            "schema_version": "1.0",
            "estate_id": inventory["estate_id"],
            "target_stack_version": snapshot["installer"]["target_version"],
        },
        "migration plan",
    )
    steps = plan["steps"]
    expect([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "migration order must be contiguous")
    expect(len({step["step_id"] for step in steps}) == len(steps), "migration step_id values must be unique")
    plan_ids = [step["transition_id"] for step in steps]
    expected_ids = expected_transition_ids(inventory, snapshot)
    expect(len(plan_ids) == len(set(plan_ids)), "migration transitions must not be duplicated")
    expect(set(plan_ids) == expected_ids, "migration plan does not cover every inventory transition")

    transitions = {item["transition_id"]: item for item in snapshot["allowed_transitions"]}
    gate_catalog = snapshot["gate_catalog"]
    for step in steps:
        transition = transitions[step["transition_id"]]
        for field in (
            "component",
            "from_version",
            "target_component",
            "target_version",
            "action",
        ):
            expect(step[field] == transition[field], f"{step['transition_id']} has wrong {field}")
        actual_gate_ids = [gate["gate_id"] for gate in step["gates"]]
        expect(actual_gate_ids == transition["required_gates"], f"{step['transition_id']} gates are incomplete or reordered")
        for gate in step["gates"]:
            expect(gate["condition"] == gate_catalog[gate["gate_id"]], f"{gate['gate_id']} does not state its pinned condition")

    order = {transition_id: index for index, transition_id in enumerate(plan_ids)}
    for before, after in snapshot["required_precedence"]:
        expect(order[before] < order[after], f"migration order must place {before} before {after}")
    covered_components = {step["component"] for step in steps}
    inventory_components = {item["component"] for item in inventory["components"]}
    expect(inventory_components <= covered_components, "migration plan does not name every estate component")


def validate_stdlib_package() -> None:
    package = ROOT / "vcf_architect"
    source_files = sorted(package.glob("*.py"))
    expect(len(source_files) >= 2, "vcf_architect must be a multi-module Python package")
    local_modules = {path.stem for path in source_files}
    for path in source_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module.split(".")[0]]
            for module in imported:
                expect(
                    module in sys.stdlib_module_names or module in local_modules or module == "vcf_architect",
                    f"non-stdlib import {module!r} in {path.relative_to(ROOT)}",
                )


def verify_reproducible_build() -> None:
    artifact_names = ("sddc-spec.json", "architecture.json", "migration-plan.json")
    with tempfile.TemporaryDirectory(prefix="vcf-architecture-") as temporary:
        output = Path(temporary) / "build"
        command = [
            sys.executable,
            "-m",
            "vcf_architect",
            "--requirements",
            "inputs/greenfield-requirements.json",
            "--estate",
            "inputs/estate-inventory.json",
            "--compatibility",
            "compatibility/pinned-compatibility.json",
            "--output",
            str(output),
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        expect(completed.returncode == 0, f"package build failed: {completed.stderr.strip()}")
        for name in artifact_names:
            generated = read_json(output / name)
            committed = read_json(ROOT / "build" / name)
            expect(generated == committed, f"package output for {name} differs from committed artifact")

        # Exercise all three input arguments with deterministic variations so a
        # fixed-output implementation cannot satisfy the data-driven contract.
        requirements = copy.deepcopy(read_json(ROOT / "inputs" / "greenfield-requirements.json"))
        inventory = copy.deepcopy(read_json(ROOT / "inputs" / "estate-inventory.json"))
        snapshot = copy.deepcopy(read_json(ROOT / "compatibility" / "pinned-compatibility.json"))
        requirements["design_id"] = "northstar-data-driven-check"
        requirements["vcf_instance_name"] = "Northstar Data Driven Check"
        requirements["networking"]["management"]["vlan_id"] += 1
        requirements["service_demand"]["vcf_automation"]["concurrent_requests"] += 1
        inventory["estate_id"] = "northstar-data-driven-estate"
        snapshot["service_profiles"]["VCF Automation"]["capacity"]["concurrent_requests"] += 1
        snapshot["gate_catalog"]["backup-restore-tested"] += " Data-driven verification marker."

        varied_inputs = {
            "requirements.json": requirements,
            "estate.json": inventory,
            "compatibility.json": snapshot,
        }
        for name, value in varied_inputs.items():
            (Path(temporary) / name).write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        varied_output = Path(temporary) / "varied-build"
        varied_command = [
            sys.executable,
            "-m",
            "vcf_architect",
            "--requirements",
            str(Path(temporary) / "requirements.json"),
            "--estate",
            str(Path(temporary) / "estate.json"),
            "--compatibility",
            str(Path(temporary) / "compatibility.json"),
            "--output",
            str(varied_output),
        ]
        varied = subprocess.run(
            varied_command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        expect(varied.returncode == 0, f"data-driven package build failed: {varied.stderr.strip()}")
        varied_sddc = read_json(varied_output / "sddc-spec.json")
        varied_architecture = read_json(varied_output / "architecture.json")
        varied_plan = read_json(varied_output / "migration-plan.json")
        expect(varied_sddc.get("vcfInstanceName") == requirements["vcf_instance_name"], "requirements input is ignored")
        management_network = next(
            item for item in varied_sddc.get("networkSpecs", []) if item.get("networkType") == "MANAGEMENT"
        )
        expect(
            management_network.get("vlanId") == requirements["networking"]["management"]["vlan_id"],
            "requirements networking input is ignored",
        )
        expect(varied_architecture.get("design_id") == requirements["design_id"], "requirements design input is ignored")
        automation = next(
            item
            for item in varied_architecture.get("service_placements", [])
            if item.get("component") == "VCF Automation"
        )
        expect(
            automation.get("demand") == requirements["service_demand"]["vcf_automation"],
            "requirements demand input is ignored",
        )
        expect(
            automation.get("capacity") == snapshot["service_profiles"]["VCF Automation"]["capacity"],
            "compatibility profile input is ignored",
        )
        expect(varied_plan.get("estate_id") == inventory["estate_id"], "estate input is ignored")
        first_gate = next(
            gate
            for step in varied_plan.get("steps", [])
            for gate in step.get("gates", [])
            if gate.get("gate_id") == "backup-restore-tested"
        )
        expect(
            first_gate.get("condition") == snapshot["gate_catalog"]["backup-restore-tested"],
            "compatibility gate input is ignored",
        )


def main() -> int:
    # Contractual first check: validate the submitted SddcSpec with the installer schema.
    sddc_path = ROOT / "build" / "sddc-spec.json"
    openapi_path = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
    sddc = read_json(sddc_path)
    openapi = read_json(openapi_path)
    sddc_schema = json_pointer(openapi, "#/components/schemas/SddcSpec")
    schema_errors = validate_json_schema(sddc, sddc_schema, openapi)
    if schema_errors:
        fail("SddcSpec fails the VCF Installer 9.1 schema:\n" + "\n".join(schema_errors[:20]))
    print("PASS installer-schema: build/sddc-spec.json is a valid SddcSpec")

    # Only after installer-schema success may the verifier inspect the other contracts.
    expected_hash = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"
    expect(hashlib.sha256(openapi_path.read_bytes()).hexdigest() == expected_hash, "installer schema hash mismatch")
    validate_research_record(read_json(ROOT / "research-sources.json"))
    print("PASS research: timestamped Broadcom sources and claims")
    requirements = read_json(ROOT / "inputs" / "greenfield-requirements.json")
    inventory = read_json(ROOT / "inputs" / "estate-inventory.json")
    snapshot = read_json(ROOT / "compatibility" / "pinned-compatibility.json")
    validate_sddc_semantics(sddc, requirements, snapshot)
    print("PASS greenfield-sddc: versions, hosts, networks, and management services")

    architecture = read_json(ROOT / "build" / "architecture.json")
    validate_architecture(architecture, requirements, snapshot)
    print("PASS architecture: site capacity, availability, placement, and sizing")

    plan = read_json(ROOT / "build" / "migration-plan.json")
    migration_schema = read_json(ROOT / "schemas" / "migration-plan.schema.json")
    plan_errors = validate_json_schema(plan, migration_schema, migration_schema)
    if plan_errors:
        fail("migration plan schema errors:\n" + "\n".join(plan_errors[:20]))
    validate_migration(plan, inventory, snapshot)
    print("PASS migration: full inventory, supported transitions, gates, and ordering")

    validate_stdlib_package()
    verify_reproducible_build()
    print("PASS package: stdlib-only and reproduces all graded artifacts")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
