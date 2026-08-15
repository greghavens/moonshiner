#!/usr/bin/env python3
"""Protected, offline acceptance checks for the VCF architecture bundle."""

from __future__ import annotations

import ast
from datetime import date
import hashlib
import ipaddress
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


ROOT = Path(__file__).resolve().parents[1]
OPENAPI_SHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"
OUTPUT_NAMES = ("greenfield-sddc.json", "topology.json", "migration-plan.json")
SECRET = re.compile(r"^\$\{[A-Z][A-Z0-9_]*\}$")


class CheckError(AssertionError):
    pass


def fail(message: str) -> None:
    raise CheckError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def json_type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return True


def resolve_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"only local JSON references are supported: {pointer}")
    value = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        require(isinstance(value, dict) and part in value, f"unresolved JSON reference: {pointer}")
        value = value[part]
    return value


def schema_errors(instance: Any, schema: Any, document: Any, path: str = "$") -> list[str]:
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: rejected by false schema"]
    if not isinstance(schema, dict):
        return [f"{path}: malformed schema node"]
    if "$ref" in schema:
        return schema_errors(instance, resolve_pointer(document, schema["$ref"]), document, path)

    errors: list[str] = []
    for index, branch in enumerate(schema.get("allOf", [])):
        errors.extend(schema_errors(instance, branch, document, path))
    if "anyOf" in schema:
        branches = [schema_errors(instance, branch, document, path) for branch in schema["anyOf"]]
        if not any(not branch_errors for branch_errors in branches):
            errors.append(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        count = sum(not schema_errors(instance, branch, document, path) for branch in schema["oneOf"])
        if count != 1:
            errors.append(f"{path}: satisfies {count} oneOf branches")
    if "not" in schema and not schema_errors(instance, schema["not"], document, path):
        errors.append(f"{path}: matches forbidden schema")

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value is outside enum")

    expected = schema.get("type")
    if expected:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(json_type_matches(instance, choice) for choice in choices):
            errors.append(f"{path}: expected type {expected!r}, got {type(instance).__name__}")
            return errors

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child = f"{path}.{key}"
            if key in properties:
                errors.extend(schema_errors(value, properties[key], document, child))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child}: additional property is forbidden")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(schema_errors(value, schema["additionalProperties"], document, child))
        if len(instance) < schema.get("minProperties", 0):
            errors.append(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            errors.append(f"{path}: too many properties")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(instance):
                errors.extend(schema_errors(item, item_schema, document, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: string does not match pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: number is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: number is not below exclusiveMaximum")
    return errors


def validate_schema(instance: Any, schema: Any, document: Any, label: str) -> None:
    errors = schema_errors(instance, schema, document)
    if errors:
        fail(f"{label} schema validation failed:\n" + "\n".join(errors[:20]))


def nested_get(value: dict[str, Any], dotted: str) -> Any:
    current: Any = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            fail(f"greenfield-sddc.json is missing required version field {dotted}")
        current = current[part]
    return current


def check_password_placeholders(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if "password" in key.lower():
                require(isinstance(child, str) and SECRET.fullmatch(child) is not None, f"{child_path} must be an environment-style secret placeholder")
            else:
                check_password_placeholders(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_password_placeholders(child, f"{path}[{index}]")


def expected_hosts(requirements: dict[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for site in requirements["data_sites"]:
        for hostname in site["hosts"]:
            result[hostname] = {
                "site": site["id"],
                "failure_domain": site["failure_domain"],
            }
    return result


def check_python_package() -> None:
    package = ROOT / "vcf_arch"
    require((package / "__init__.py").is_file(), "missing stdlib package vcf_arch/__init__.py")
    require((package / "__main__.py").is_file(), "missing package entry point vcf_arch/__main__.py")
    py_files = sorted(package.rglob("*.py"))
    require(py_files, "vcf_arch contains no Python source")
    stdlib = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    for path in py_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            fail(f"invalid Python in {path.relative_to(ROOT)}: {exc}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for module in roots:
                require(module in stdlib or module == "vcf_arch", f"non-stdlib import {module!r} in {path.relative_to(ROOT)}")


def run_builder(destination: Path) -> dict[str, bytes]:
    command = [
        sys.executable,
        "-m",
        "vcf_arch",
        "--requirements",
        "fixtures/design_requirements.json",
        "--estate",
        "fixtures/estate_inventory.json",
        "--compatibility",
        "fixtures/compatibility_snapshot.json",
        "--output",
        os.fspath(destination),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ""},
    )
    require(completed.returncode == 0, f"vcf_arch command failed:\n{completed.stdout}\n{completed.stderr}")
    produced: dict[str, bytes] = {}
    for name in OUTPUT_NAMES:
        path = destination / name
        require(path.is_file(), f"vcf_arch did not produce {name}")
        produced[name] = path.read_bytes()
        try:
            json.loads(produced[name])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            fail(f"vcf_arch produced invalid JSON in {name}: {exc}")
    return produced


def check_determinism_and_checked_in_outputs() -> None:
    with tempfile.TemporaryDirectory(prefix="vcf-architecture-a-") as first_dir, tempfile.TemporaryDirectory(prefix="vcf-architecture-b-") as second_dir:
        first = run_builder(Path(first_dir))
        second = run_builder(Path(second_dir))
    require(first == second, "vcf_arch output is not byte-for-byte deterministic")
    for name, content in first.items():
        checked_in = ROOT / "architecture" / name
        require(checked_in.is_file(), f"missing checked-in architecture/{name}")
        require(checked_in.read_bytes() == content, f"checked-in architecture/{name} is stale relative to vcf_arch output")


def check_research() -> None:
    research = load_json(ROOT / "architecture" / "research-consulted.json")
    sources = research.get("sources") if isinstance(research, dict) else None
    require(isinstance(sources, list) and len(sources) >= 2, "research-consulted.json must contain at least two sources")
    urls: set[str] = set()
    has_compatibility = False
    has_upgrade_guidance = False
    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        require(isinstance(source, dict), f"{label} must be an object")
        require(set(source) == {"url", "title", "accessed_on", "fact_used"}, f"{label} has missing or unexpected fields")
        for field in ("url", "title", "accessed_on", "fact_used"):
            require(isinstance(source[field], str) and source[field].strip() == source[field] and bool(source[field]), f"{label} has an invalid {field}")
        parsed = urlparse(source["url"])
        hostname = (parsed.hostname or "").lower()
        require(parsed.scheme == "https" and (hostname == "broadcom.com" or hostname.endswith(".broadcom.com")), f"{label} must use a real Broadcom HTTPS URL")
        require(".invalid" not in hostname and source["url"] not in urls, f"{label} has a duplicate or fixture URL")
        urls.add(source["url"])
        try:
            date.fromisoformat(source["accessed_on"])
        except ValueError:
            fail(f"{label} accessed_on must be an ISO YYYY-MM-DD date")
        combined = (source["title"] + " " + source["fact_used"] + " " + parsed.path).lower()
        has_compatibility = has_compatibility or "compatib" in combined or "interop" in combined
        has_upgrade_guidance = has_upgrade_guidance or "upgrade" in combined
    require(has_compatibility, "research sources do not record a compatibility or interoperability fact")
    require(has_upgrade_guidance, "research sources do not record an upgrade-guidance fact")


def check_sddc_spec(
    spec: dict[str, Any],
    requirements: dict[str, Any],
    compatibility: dict[str, Any],
    openapi: dict[str, Any],
) -> None:
    validate_schema(spec, {"$ref": "#/components/schemas/SddcSpec"}, openapi, "greenfield-sddc.json")
    domain = requirements["domain"]
    require(spec.get("sddcId") == domain["sddc_id"], "SddcSpec sddcId does not match requirements")
    require(spec.get("vcfInstanceName") == domain["vcf_instance_name"], "SddcSpec vcfInstanceName does not match requirements")
    require(spec.get("workflowType") == "VCF", "SddcSpec workflowType must be VCF")
    require(spec.get("clusterSpec", {}).get("datacenterName") == domain["datacenter_name"], "wrong datacenter name")
    require(spec.get("clusterSpec", {}).get("clusterName") == domain["cluster_name"], "wrong cluster name")

    wanted_hosts = set(expected_hosts(requirements))
    host_specs = spec.get("hostSpecs")
    require(isinstance(host_specs, list), "SddcSpec hostSpecs must be an array")
    actual_hosts = [item.get("hostname") for item in host_specs if isinstance(item, dict)]
    require(len(actual_hosts) == len(host_specs), "each SddcSpec hostSpec must name a host")
    require(len(actual_hosts) == len(set(actual_hosts)), "SddcSpec hostnames must be unique")
    require(set(actual_hosts) == wanted_hosts, "SddcSpec data-host inventory does not exactly match both required sites")
    require(requirements["witness"]["hostname"] not in set(actual_hosts), "vSAN witness must not be included in SddcSpec data hosts")

    wanted_networks = {item["networkType"]: item for item in requirements["networks"]}
    actual_networks = {item.get("networkType"): item for item in spec.get("networkSpecs", []) if isinstance(item, dict)}
    require(set(actual_networks) == set(wanted_networks), "SddcSpec network types do not match requirements")
    for network_type, wanted in wanted_networks.items():
        actual = actual_networks[network_type]
        for key, value in wanted.items():
            require(actual.get(key) == value, f"{network_type} network has wrong {key}")

    dns = requirements["dns"]
    require(spec.get("dnsSpec") == dns, "SddcSpec DNS configuration does not match requirements")
    require(spec.get("ntpServers") == requirements["ntp_servers"], "SddcSpec NTP servers do not match requirements")

    ftt = requirements["availability"]["failures_to_tolerate"]
    vsan = spec.get("datastoreSpec", {}).get("vsanSpec", {})
    require(vsan.get("failuresToTolerate") == ftt, "SddcSpec failuresToTolerate contradicts the stated availability requirement")
    require(vsan.get("esaConfig", {}).get("enabled") is True, "the required vSAN ESA design is not enabled")

    for dotted, expected in compatibility["greenfield"]["sddc_spec_version_fields"].items():
        require(nested_get(spec, dotted) == expected, f"unsupported component version at {dotted}")

    appliances = requirements["appliances"]
    require(spec.get("vcenterSpec", {}).get("vcenterHostname") == appliances["vcenter"], "wrong vCenter placement")
    require(spec.get("sddcManagerSpec", {}).get("hostname") == appliances["sddc_manager"], "wrong SDDC Manager placement")
    nsxt = spec.get("nsxtSpec", {})
    require(nsxt.get("vipFqdn") == appliances["nsx_vip"], "wrong NSX VIP")
    require([item.get("hostname") for item in nsxt.get("nsxtManagers", [])] == appliances["nsx_managers"], "three required NSX managers are not present")
    operations = spec.get("vcfOperationsSpec", {})
    require(operations.get("loadBalancerFqdn") == appliances["vcf_operations_load_balancer"], "wrong VCF Operations load balancer")
    require([item.get("hostname") for item in operations.get("nodes", [])] == appliances["vcf_operations_nodes"], "wrong VCF Operations node set")

    pool = spec.get("vspClusterSpec", {}).get("ipv4Pool", {})
    wanted_pool = requirements["management_services_ipv4"]
    require(pool.get("cidr") == wanted_pool["cidr"], "wrong management-services IPv4 CIDR")
    require(pool.get("addresses") == wanted_pool["addresses"], "wrong management-services IPv4 addresses")
    minimum_ips = compatibility["greenfield"]["minimum_management_services_ipv4_addresses"]
    require(len(pool.get("addresses", [])) >= minimum_ips, f"VCF Management Services requires at least {minimum_ips} IPv4 addresses")
    network = ipaddress.ip_network(pool["cidr"])
    require(all(ipaddress.ip_address(address) in network for address in pool["addresses"]), "management-services address falls outside its CIDR")

    required_secret_paths = (
        "vcenterSpec.rootVcenterPassword",
        "sddcManagerSpec.rootPassword",
        "sddcManagerSpec.sshPassword",
        "sddcManagerSpec.localUserPassword",
        "nsxtSpec.rootNsxtManagerPassword",
        "nsxtSpec.nsxtAdminPassword",
        "nsxtSpec.nsxtAuditPassword",
        "vspClusterSpec.systemUserPassword",
        "vcfOperationsSpec.adminUserPassword",
    )
    for dotted in required_secret_paths:
        value = nested_get(spec, dotted)
        require(isinstance(value, str) and SECRET.fullmatch(value) is not None, f"{dotted} must be an environment-style secret placeholder")
    check_password_placeholders(spec)


def close_enough(actual: Any, expected: float) -> bool:
    return isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isclose(float(actual), expected, rel_tol=0, abs_tol=1e-6)


def check_topology(
    topology: dict[str, Any],
    spec: dict[str, Any],
    requirements: dict[str, Any],
    compatibility: dict[str, Any],
) -> None:
    require(topology.get("schema_version") == "1.0", "topology schema_version must be 1.0")
    require(topology.get("architecture_id") == requirements["architecture_id"], "topology architecture_id is wrong")
    domain = topology.get("management_domain", {})
    require(domain.get("sddc_id") == requirements["domain"]["sddc_id"], "topology SDDC id is wrong")
    require(domain.get("name") == requirements["domain"]["management_domain_name"], "topology management-domain name is wrong")
    require(domain.get("stretched") is True, "management domain must be stretched")
    ftt = requirements["availability"]["failures_to_tolerate"]
    require(domain.get("failures_to_tolerate") == ftt, "topology FTT contradicts requirements")

    wanted = expected_hosts(requirements)
    placements = topology.get("host_placements")
    require(isinstance(placements, list), "topology host_placements must be an array")
    by_host: dict[str, dict[str, Any]] = {}
    for placement in placements:
        require(isinstance(placement, dict), "invalid host placement")
        hostname = placement.get("hostname")
        require(hostname not in by_host, "duplicate host placement")
        by_host[hostname] = placement
    require(set(by_host) == set(wanted), "topology placements do not cover exactly the SddcSpec data hosts")
    require(set(by_host) == {item["hostname"] for item in spec["hostSpecs"]}, "topology and SddcSpec host sets differ")
    counts: dict[str, int] = {}
    for hostname, expected in wanted.items():
        placement = by_host[hostname]
        require(placement.get("site") == expected["site"], f"{hostname} is in the wrong data site")
        require(placement.get("failure_domain") == expected["failure_domain"], f"{hostname} is in the wrong failure domain")
        require(placement.get("role") == "data-host", f"{hostname} must be a data host")
        counts[expected["site"]] = counts.get(expected["site"], 0) + 1

    minimum_by_ftt = compatibility["greenfield"]["minimum_data_hosts_per_site_by_failures_to_tolerate"]
    require(str(ftt) in minimum_by_ftt, f"pinned compatibility has no host rule for failuresToTolerate={ftt}")
    minimum_hosts = minimum_by_ftt[str(ftt)]
    expected_per_site = requirements["availability"]["data_hosts_per_site"]
    for site in requirements["data_sites"]:
        count = counts.get(site["id"], 0)
        require(count >= minimum_hosts, f"host count {count} at {site['id']} contradicts failuresToTolerate={ftt}; need at least {minimum_hosts}")
        require(count == expected_per_site, f"host count {count} at {site['id']} does not meet the stated capacity design of {expected_per_site}")

    witness = topology.get("witness", {})
    wanted_witness = requirements["witness"]
    for key, value in wanted_witness.items():
        require(witness.get(key) == value, f"vSAN witness has wrong {key}")
    data_site_ids = {site["id"] for site in requirements["data_sites"]}
    data_failure_domains = {site["failure_domain"] for site in requirements["data_sites"]}
    require(witness.get("site") not in data_site_ids, "witness must be outside both data sites")
    require(witness.get("failure_domain") not in data_failure_domains, "witness must use an independent third failure domain")
    require(witness.get("included_in_sddc_host_specs") is False, "witness must not be counted as an SddcSpec data host")
    require(witness.get("hostname") not in by_host, "witness must not be counted in host placement capacity")
    require(witness.get("target_version") == compatibility["greenfield"]["supported_combination"]["vsan_witness"], "unsupported witness target version")

    profile = requirements["host_profile"]
    require(topology.get("hardware_profile_id") == profile["hardware_profile_id"], "wrong hardware profile")
    require(profile["hardware_profile_id"] in compatibility["greenfield"]["supported_hardware_profiles"], "hardware profile is absent from pinned compatibility")
    total_hosts = len(wanted)
    surviving_hosts = min(counts.values())
    calculated = {
        "normal": {
            "data_hosts": total_hosts,
            "physical_cores": total_hosts * profile["physical_cores"],
            "memory_gib": total_hosts * profile["memory_gib"],
            "raw_storage_tb": total_hosts * profile["raw_storage_tb"],
        },
        "after_one_data_site_failure": {
            "data_hosts": surviving_hosts,
            "physical_cores": surviving_hosts * profile["physical_cores"],
            "memory_gib": surviving_hosts * profile["memory_gib"],
            "raw_storage_tb": surviving_hosts * profile["raw_storage_tb"],
        },
    }
    capacity = topology.get("capacity", {})
    for state, values in calculated.items():
        actual = capacity.get(state, {})
        for key, expected in values.items():
            require(close_enough(actual.get(key), expected), f"topology {state} capacity has wrong {key}")
    minimum = requirements["minimum_capacity_after_one_data_site_failure"]
    surviving = calculated["after_one_data_site_failure"]
    for key, required_value in minimum.items():
        require(surviving[key] >= required_value, f"architecture does not meet surviving {key} capacity")
    require(capacity.get("minimum_required_after_one_data_site_failure") == minimum, "topology must carry the stated surviving-capacity requirement")

    software = topology.get("software", {})
    for component, expected in compatibility["greenfield"]["supported_combination"].items():
        require(software.get(component) == expected, f"topology software combination has wrong {component} version")


def check_migration(
    plan: dict[str, Any],
    estate: dict[str, Any],
    compatibility: dict[str, Any],
    plan_schema: dict[str, Any],
) -> None:
    validate_schema(plan, plan_schema, plan_schema, "migration-plan.json")
    require(plan["estate_id"] == estate["estate_id"], "migration plan estate_id is wrong")
    require(plan["source_vcf_version"] == estate["source_vcf_version"], "migration plan source version is wrong")
    require(plan["target_vcf_version"] == estate["target_vcf_version"], "migration plan target version is wrong")
    inventory = {item["id"]: item for item in estate["components"]}
    steps = plan["steps"]
    require(len(steps) == len(inventory), "migration plan must name every inventory component exactly once")
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "migration step order must be contiguous from 1")
    by_component: dict[str, dict[str, Any]] = {}
    position: dict[str, int] = {}
    targets = compatibility["migration"]["component_targets"]
    for step in steps:
        component_id = step["component_id"]
        require(component_id not in by_component, f"duplicate migration component {component_id}")
        require(component_id in inventory, f"migration component {component_id} is not in the estate inventory")
        require(component_id in targets, f"pinned compatibility has no target for {component_id}")
        item = inventory[component_id]
        authority = targets[component_id]
        require(step["component"] == item["name"], f"wrong component name for {component_id}")
        require(step["current_version"] == item["version"], f"wrong current version for {component_id}")
        require(step["target"] == authority["target"], f"unsupported target for {component_id}")
        require(step["action"] == authority["action"], f"wrong migration action for {component_id}")
        require(set(step["gates"]) == set(authority["gates"]) and len(step["gates"]) == len(authority["gates"]), f"wrong gates for {component_id}")
        by_component[component_id] = step
        position[component_id] = step["order"]
    require(set(by_component) == set(inventory), "migration plan component coverage differs from inventory")
    for predecessor, successor in compatibility["migration"]["required_precedence"]:
        require(position[predecessor] < position[successor], f"migration order violates {predecessor} before {successor}")

    facts = estate["facts"]
    sans = facts.get("operations_for_networks_certificate_sans", [])
    require(any("." in value and not re.fullmatch(r"[0-9.]+", value) for value in sans), "operations-for-networks certificate lacks an FQDN SAN")
    require(any(_is_ip(value) for value in sans), "operations-for-networks certificate lacks an IP SAN")
    require(facts.get("identity_broker_network") == "management", "identity broker is not on the management network")


def _is_ip(value: Any) -> bool:
    try:
        ipaddress.ip_address(value)
    except (ValueError, TypeError):
        return False
    return True


def main() -> int:
    try:
        requirements = load_json(ROOT / "fixtures" / "design_requirements.json")
        estate = load_json(ROOT / "fixtures" / "estate_inventory.json")
        compatibility = load_json(ROOT / "fixtures" / "compatibility_snapshot.json")
        plan_schema = load_json(ROOT / "fixtures" / "migration-plan.schema.json")
        openapi_path = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
        require(hashlib.sha256(openapi_path.read_bytes()).hexdigest() == OPENAPI_SHA256, "pinned VCF Installer OpenAPI document was modified")
        openapi = load_json(openapi_path)
        require(openapi.get("info", {}).get("version") == "9.1.0.0", "wrong VCF Installer OpenAPI version")

        check_python_package()
        check_determinism_and_checked_in_outputs()
        check_research()
        spec = load_json(ROOT / "architecture" / "greenfield-sddc.json")
        topology = load_json(ROOT / "architecture" / "topology.json")
        migration = load_json(ROOT / "architecture" / "migration-plan.json")
        check_sddc_spec(spec, requirements, compatibility, openapi)
        check_topology(topology, spec, requirements, compatibility)
        check_migration(migration, estate, compatibility, plan_schema)
    except (CheckError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.1 architecture bundle satisfies the pinned design and migration authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
