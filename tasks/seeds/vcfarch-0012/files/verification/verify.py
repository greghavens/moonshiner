#!/usr/bin/env python3
"""Deterministic offline verifier for the VCF architecture seed."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]

# Filled from the seed baseline. The verifier also validates these only after the
# installer-schema check, preserving the required validation order.
PROTECTED_SHA256 = {
    "compatibility/compatibility-snapshot.json": "d744bbbcfc2cccc03478f0479ba1511d942a93b7879925e2dc9b1757b0c1b660",
    "fixtures/design-requirements.json": "0c848bf413d0f3ec631deedc74aac9c4c29cecf8750dce5d4e3563a9e9e78ccb",
    "fixtures/estate-inventory.json": "e5388e1fca14b9414dfb2d5af9690e67b3fad0d487873579def4267204c0b989",
    "schemas/migration-plan.schema.json": "ffc4a7b01e64223400aaf30439fb5c0a32d131636da73abfb7103f6b010a75df",
    "specifications/vcf-installer/LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    "specifications/vcf-installer/vcf-installer-openapi.json": "9295f4d07b46343600da2e4a609e166ec48feabcf2189bc20c2f90c9f4174b72",
}


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: line {exc.lineno}: {exc.msg}"
        ) from exc


def json_pointer(document: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        raise VerificationError(f"unsupported non-local schema reference: {ref}")
    node = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[int(part)] if isinstance(node, list) else node[part]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise VerificationError(f"unresolvable schema reference: {ref}") from exc
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
    raise VerificationError(f"unsupported JSON Schema type: {expected}")


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    path: str = "$",
) -> list[str]:
    if "$ref" in schema:
        return validate_json_schema(value, json_pointer(document, schema["$ref"]), document, path)

    errors: list[str] = []
    if "allOf" in schema:
        for item in schema["allOf"]:
            errors.extend(validate_json_schema(value, item, document, path))
    if "anyOf" in schema:
        branches = [validate_json_schema(value, item, document, path) for item in schema["anyOf"]]
        if not any(not branch for branch in branches):
            errors.append(f"{path}: does not match any allowed schema")
    if "oneOf" in schema:
        matches = sum(
            not validate_json_schema(value, item, document, path) for item in schema["oneOf"]
        )
        if matches != 1:
            errors.append(f"{path}: must match exactly one allowed schema")

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        matched_type = any(type_matches(value, item) for item in expected_type)
    elif expected_type is None:
        matched_type = True
    else:
        matched_type = type_matches(value, expected_type)
    if not matched_type:
        errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
        return errors

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                errors.extend(validate_json_schema(child, properties[key], document, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    validate_json_schema(
                        child, schema["additionalProperties"], document, f"{path}.{key}"
                    )
                )

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: has more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, child in enumerate(value):
                errors.extend(
                    validate_json_schema(child, schema["items"], document, f"{path}[{index}]")
                )

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value) is not None
            except re.error as exc:
                raise VerificationError(f"invalid pattern in pinned schema at {path}: {exc}") from exc
            if not matched:
                errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} exceeds maximum {schema['maximum']}")
    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def check_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError as exc:
            raise VerificationError(f"protected file is missing: {relative}") from exc
        require(actual == expected, f"protected file changed: {relative}")


def get_network(spec: dict[str, Any], network_type: str) -> dict[str, Any]:
    matches = [item for item in spec.get("networkSpecs", []) if item.get("networkType") == network_type]
    require(len(matches) == 1, f"SddcSpec must contain exactly one {network_type} network")
    return matches[0]


def check_research_sources(sources: Any) -> None:
    require(isinstance(sources, list) and sources, "research-sources.json must be a non-empty array")
    required_fields = {"title", "publisher", "url", "accessed_at", "supported_claims"}
    urls: list[str] = []
    searchable: list[str] = []
    for index, source in enumerate(sources):
        require(isinstance(source, dict), f"research source {index + 1} must be an object")
        require(
            required_fields.issubset(source),
            f"research source {index + 1} is missing required fields",
        )
        for field in ("title", "publisher", "url", "accessed_at"):
            require(
                isinstance(source[field], str) and source[field].strip(),
                f"research source {index + 1} has an empty {field}",
            )
        try:
            parsed_date = date.fromisoformat(source["accessed_at"])
        except ValueError as exc:
            raise VerificationError(
                f"research source {index + 1} accessed_at must be an ISO date"
            ) from exc
        require(
            source["accessed_at"] == parsed_date.isoformat(),
            f"research source {index + 1} accessed_at must use YYYY-MM-DD",
        )

        parsed_url = urlparse(source["url"])
        hostname = (parsed_url.hostname or "").lower()
        is_broadcom_publication = hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        is_vmware_vcf_blog = hostname == "blogs.vmware.com"
        require(
            parsed_url.scheme == "https" and (is_broadcom_publication or is_vmware_vcf_blog),
            f"research source {index + 1} must be a public Broadcom-published HTTPS URL",
        )
        claims = source["supported_claims"]
        require(
            isinstance(claims, list)
            and claims
            and all(isinstance(claim, str) and len(claim.strip()) >= 8 for claim in claims),
            f"research source {index + 1} must record non-empty supported claims",
        )
        urls.append(source["url"])
        searchable.append(
            " ".join([source["title"], source["url"], *claims]).lower()
        )

    require(len(urls) == len(set(urls)), "research source URLs must be unique")
    require(
        any("interop" in item or "compatib" in item for item in searchable),
        "research record must include compatibility or interoperability material",
    )
    require(
        any("upgrade" in item and "9.1" in item for item in searchable),
        "research record must include VCF 9.1 upgrade-path guidance",
    )


def check_greenfield(
    spec: dict[str, Any],
    requirements: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    frozen = snapshot["greenfield"]
    versions = frozen["component_versions"]
    hosts = requirements["hosts"]

    require(spec.get("version") == snapshot["target_vcf_version"], "wrong SddcSpec version")
    require(spec.get("workflowType") == "VCF", "workflowType must be VCF")
    require(spec.get("sddcId") == "dal01-m01", "unexpected management-domain identifier")
    require(spec.get("vcfInstanceName") == requirements["architecture_id"], "wrong VCF instance name")

    actual_hosts = [item.get("hostname") for item in spec.get("hostSpecs", [])]
    expected_hosts = [item["hostname"] for item in hosts]
    require(actual_hosts == expected_hosts, "SddcSpec host list must match the fixture in order")
    require(
        len(actual_hosts) == frozen["minimum_host_count"],
        "consolidated design must use exactly the minimum supported host count",
    )
    resource_pools = spec.get("clusterSpec", {}).get("resourcePoolSpecs", [])
    require(
        [pool.get("type") for pool in resource_pools] == ["management", "compute"],
        "consolidated cluster must define management and compute resource pools",
    )
    require(
        all(pool.get("name") for pool in resource_pools),
        "consolidated resource pools must be named",
    )
    require(
        resource_pools[0].get("cpuReservationPercentage", 0) > 0
        and resource_pools[0].get("memoryReservationPercentage", 0) > 0,
        "management resource pool must reserve CPU and memory",
    )

    fqdns = requirements["appliance_fqdns"]
    vcenter = spec.get("vcenterSpec", {})
    require(vcenter.get("vcenterHostname") == fqdns["vcenter"], "wrong vCenter FQDN")
    require(vcenter.get("version") == versions["vcenter_server"], "wrong vCenter target build")
    require(vcenter.get("useExistingDeployment") is False, "greenfield vCenter cannot be existing")

    sddc_manager = spec.get("sddcManagerSpec", {})
    require(sddc_manager.get("hostname") == fqdns["sddc_manager"], "wrong SDDC Manager FQDN")
    require(sddc_manager.get("version") == versions["sddc_manager"], "wrong SDDC Manager target")
    require(sddc_manager.get("useExistingDeployment") is False, "greenfield SDDC Manager cannot be existing")

    nsx = spec.get("nsxtSpec", {})
    require(nsx.get("version") == versions["nsx"], "wrong NSX target build")
    require(nsx.get("vipFqdn") == fqdns["nsx_vip"], "wrong NSX VIP FQDN")
    require(
        [item.get("hostname") for item in nsx.get("nsxtManagers", [])]
        == fqdns["nsx_managers"],
        "NSX manager node set does not match the requirements",
    )
    require(
        len(nsx.get("nsxtManagers", [])) == frozen["required_nsx_manager_nodes"],
        "wrong NSX manager node count",
    )

    ops = spec.get("vcfOperationsSpec", {})
    require(ops.get("version") == versions["vcf_operations"], "wrong VCF Operations target")
    require(
        [item.get("hostname") for item in ops.get("nodes", [])] == fqdns["vcf_operations"],
        "VCF Operations node set does not match the requirements",
    )
    require(
        len(ops.get("nodes", [])) == frozen["required_vcf_operations_nodes"],
        "wrong VCF Operations node count",
    )
    require(
        ops.get("loadBalancerFqdn") == fqdns["vcf_operations_load_balancer"],
        "wrong VCF Operations load-balancer FQDN",
    )

    license_spec = spec.get("licenseServerSpec", {})
    require(license_spec.get("hostname") == fqdns["license_server"], "wrong license-server FQDN")
    require(license_spec.get("version") == versions["license_server"], "wrong license-server target")

    required_types = frozen["required_network_types"]
    fixture_networks = {item["network_type"]: item for item in requirements["networks"]}
    require(
        len(spec.get("networkSpecs", [])) == len(required_types),
        "SddcSpec must contain only the required fixture networks",
    )
    for network_type in required_types:
        actual = get_network(spec, network_type)
        expected = fixture_networks[network_type]
        require(actual.get("vlanId") == expected["vlan_id"], f"wrong VLAN for {network_type}")
        require(actual.get("subnet") == expected["cidr"], f"wrong subnet for {network_type}")
        require(actual.get("gateway") == expected["gateway"], f"wrong gateway for {network_type}")
        require(actual.get("mtu") == expected["mtu"], f"wrong MTU for {network_type}")
        require(
            actual.get("includeIpAddress") == expected["host_addresses"],
            f"wrong host address set for {network_type}",
        )

    dvs_specs = spec.get("dvsSpecs", [])
    require(len(dvs_specs) == 1, "design must use one consolidated distributed switch")
    dvs = dvs_specs[0]
    require(set(dvs.get("networks", [])) == set(required_types), "DVS must carry all required networks")
    mappings = dvs.get("vmnicsToUplinks", [])
    require(len(mappings) == frozen["minimum_dvs_uplinks"], "DVS must use two uplinks")
    require(
        {(item.get("id"), item.get("uplink")) for item in mappings}
        == {("vmnic0", "uplink1"), ("vmnic1", "uplink2")},
        "unexpected vmnic-to-uplink mapping",
    )

    dns = spec.get("dnsSpec", {})
    require(dns.get("subdomain") == requirements["dns"]["subdomain"], "wrong DNS subdomain")
    require(
        dns.get("nameservers") == requirements["dns"]["nameservers"],
        "DNS nameservers do not match the requirements",
    )
    require(
        spec.get("ntpServers") == requirements["dns"]["ntp_servers"],
        "NTP servers do not match the requirements",
    )

    tep_fixture = requirements["nsx_tep_pool"]
    tep_pool = nsx.get("ipAddressPoolSpec", {})
    require(tep_pool.get("name") == tep_fixture["name"], "wrong NSX TEP pool name")
    tep_subnets = tep_pool.get("subnets", [])
    require(len(tep_subnets) == 1, "NSX TEP pool must contain exactly one subnet")
    tep_subnet = tep_subnets[0]
    require(tep_subnet.get("cidr") == tep_fixture["cidr"], "wrong NSX TEP subnet")
    require(tep_subnet.get("gateway") == tep_fixture["gateway"], "wrong NSX TEP gateway")
    require(
        tep_subnet.get("ipAddressPoolRanges")
        == [{"start": tep_fixture["start"], "end": tep_fixture["end"]}],
        "wrong NSX TEP address range",
    )
    require(
        nsx.get("transportVlanId") == fixture_networks["HOST_OVERLAY"]["vlan_id"],
        "NSX transport VLAN does not match the host-overlay network",
    )

    vsan = spec.get("datastoreSpec", {}).get("vsanSpec", {})
    require(vsan.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")
    require(
        vsan.get("failuresToTolerate") == frozen["storage"]["failures_to_tolerate"],
        "wrong vSAN failures-to-tolerate value",
    )

    vsp = spec.get("vspClusterSpec", {})
    require(vsp.get("platformFqdn") == fqdns["vsp_platform"], "wrong VSP platform FQDN")
    require(vsp.get("instanceFqdn") == fqdns["vsp_instance"], "wrong VSP instance FQDN")
    require(vsp.get("fleetFqdn") == fqdns["vsp_fleet"], "wrong VSP fleet FQDN")
    internal_cidr = vsp.get("internalClusterCidrIpv4")
    require(
        internal_cidr in frozen["allowed_internal_cluster_cidrs_ipv4"],
        "unsupported VSP internal IPv4 CIDR",
    )
    management_network = ip_network(fixture_networks["MANAGEMENT"]["cidr"])
    require(
        not ip_network(internal_cidr).overlaps(management_network),
        "VSP internal CIDR overlaps the management network",
    )
    addresses = vsp.get("ipv4Pool", {}).get("addresses", [])
    expected_pool = requirements["management_services_ipv4_pool"]
    first = int(ip_address(expected_pool["start"]))
    last = int(ip_address(expected_pool["end"]))
    expected_addresses = [str(ip_address(number)) for number in range(first, last + 1)]
    require(addresses == expected_addresses, "management-services address pool does not match fixture")
    require(
        len(addresses) == frozen["required_management_service_ips"],
        "management-services pool must contain exactly 12 addresses",
    )
    require(all(ip_address(item) in management_network for item in addresses), "VSP address outside management network")

    capacity = requirements["capacity"]
    failed = capacity["calculate_after_host_failures"]
    remaining = len(hosts) - failed
    surviving_cores = sum(sorted(item["physical_cpu_cores"] for item in hosts)[:remaining])
    surviving_memory = sum(sorted(item["memory_gib"] for item in hosts)[:remaining])
    surviving_storage = sum(sorted(item["raw_nvme_tib"] for item in hosts)[:remaining])
    reserved = capacity["management_reserved"]
    available = {
        "vcpu": round(surviving_cores * capacity["cpu_overcommit_ratio"] - reserved["vcpu"], 2),
        "memory_gib": round(surviving_memory - reserved["memory_gib"], 2),
        "usable_storage_tib": round(
            surviving_storage
            * (1.0 - capacity["storage_free_space_reserve_fraction"])
            / capacity["storage_replica_factor"]
            - reserved["usable_storage_tib"],
            2,
        ),
    }
    required = capacity["workload_required"]
    require(
        all(available[key] >= required[key] for key in required),
        "selected minimum host set does not meet workload capacity after one host failure",
    )


def check_migration(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    migration = snapshot["migration"]
    require(plan["estate_id"] == inventory["estate_id"], "migration estate_id does not match inventory")
    require(plan["source_vcf_version"] == inventory["vcf_version"], "wrong source VCF version")
    require(plan["source_vcf_version"] in migration["source_vcf_versions"], "unsupported source VCF version")
    require(plan["target_vcf_version"] == snapshot["target_vcf_version"], "wrong migration target")

    contracts = migration["ordered_steps"]
    steps = plan["steps"]
    require(len(steps) == len(contracts), "migration plan must contain all pinned phases")
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "step order must be contiguous")
    require(
        [step["step_id"] for step in steps] == [item["step_id"] for item in contracts],
        "migration phases are not in the pinned order",
    )

    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    installed_seen: set[str] = set()
    seen_steps: set[str] = set()
    for step, contract in zip(steps, contracts):
        for key in ("step_id", "component_id", "component_name", "target_component", "target_version", "action"):
            require(step[key] == contract[key], f"{step['step_id']}: wrong {key}")
        require(step["from_version"] in contract["from_versions"], f"{step['step_id']}: unsupported source version")
        component_id = step["component_id"]
        if component_id in inventory_by_id:
            installed_seen.add(component_id)
            require(step["from_version"] == inventory_by_id[component_id]["version"], f"{step['step_id']}: inventory version mismatch")
            require(step["component_name"] == inventory_by_id[component_id]["name"], f"{step['step_id']}: inventory name mismatch")
        else:
            require(step["from_version"] == "not-installed", f"{step['step_id']}: added component must start not-installed")

        gate_ids = [gate["gate_id"] for gate in step["gates"]]
        require(gate_ids == contract["required_gate_ids"], f"{step['step_id']}: missing or reordered technical gates")
        for gate in step["gates"]:
            require(gate["requires_steps"] == contract["requires_steps"], f"{step['step_id']}: wrong predecessor gate")
            require(set(gate["requires_steps"]).issubset(seen_steps), f"{step['step_id']}: gate references a later step")
        seen_steps.add(step["step_id"])

    require(installed_seen == set(inventory_by_id), "not every installed component is named exactly once")
    planned_ids = [step["component_id"] for step in steps]
    require(len(planned_ids) == len(set(planned_ids)), "component appears in more than one migration phase")


def check_stdlib_package() -> None:
    package = ROOT / "vcf_architecture"
    require((package / "__init__.py").is_file(), "missing vcf_architecture/__init__.py")
    require((package / "__main__.py").is_file(), "missing vcf_architecture/__main__.py")
    source_files = sorted(package.rglob("*.py"))
    require(bool(source_files), "Python package has no source files")
    allowed_roots = set(sys.stdlib_module_names) | {"vcf_architecture"}
    for source_file in source_files:
        try:
            tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        except (OSError, SyntaxError) as exc:
            raise VerificationError(f"cannot parse {source_file.relative_to(ROOT)}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            else:
                continue
            for root in roots:
                require(root in allowed_roots, f"non-stdlib import {root!r} in {source_file.relative_to(ROOT)}")


def check_generator(committed: dict[str, Any]) -> None:
    generated_runs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="vcf-architecture-verify-") as temp_root:
        for run_number in (1, 2):
            output = Path(temp_root) / f"run-{run_number}"
            result = subprocess.run(
                [sys.executable, "-m", "vcf_architecture", "--output-dir", str(output)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            require(
                result.returncode == 0,
                f"package generator failed (exit {result.returncode}): {result.stderr.strip()}",
            )
            current = {
                name: load_json(output / name)
                for name in ("sddc-spec.json", "migration-plan.json")
            }
            generated_runs.append(current)
    require(generated_runs[0] == generated_runs[1], "package output is not deterministic")
    require(generated_runs[0] == committed, "committed architecture artifacts differ from package output")


def main() -> int:
    try:
        # This is deliberately the first verification stage. Do not load the
        # fixtures, compatibility snapshot, research record, or package first.
        installer_document = load_json(
            ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
        )
        sddc_spec = load_json(ROOT / "architecture/sddc-spec.json")
        try:
            installer_schema = installer_document["components"]["schemas"]["SddcSpec"]
        except (KeyError, TypeError) as exc:
            raise VerificationError("pinned installer document has no SddcSpec schema") from exc
        schema_errors = validate_json_schema(
            sddc_spec, installer_schema, installer_document, "$.SddcSpec"
        )
        require(
            not schema_errors,
            "SddcSpec fails the pinned installer schema:\n  " + "\n  ".join(schema_errors),
        )

        check_protected_files()
        requirements = load_json(ROOT / "fixtures/design-requirements.json")
        inventory = load_json(ROOT / "fixtures/estate-inventory.json")
        snapshot = load_json(ROOT / "compatibility/compatibility-snapshot.json")
        research_sources = load_json(ROOT / "architecture/research-sources.json")
        migration_schema = load_json(ROOT / "schemas/migration-plan.schema.json")
        migration_plan = load_json(ROOT / "architecture/migration-plan.json")

        migration_errors = validate_json_schema(
            migration_plan, migration_schema, migration_schema, "$.migrationPlan"
        )
        require(
            not migration_errors,
            "migration-plan.json schema errors:\n  " + "\n  ".join(migration_errors),
        )

        check_greenfield(sddc_spec, requirements, snapshot)
        check_research_sources(research_sources)
        check_migration(migration_plan, inventory, snapshot)
        check_stdlib_package()
        committed = {
            "sddc-spec.json": sddc_spec,
            "migration-plan.json": migration_plan,
        }
        check_generator(committed)
    except (VerificationError, KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.1 architecture and migration artifacts satisfy the pinned contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
