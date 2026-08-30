#!/usr/bin/env python3
"""Deterministic offline verifier for the Northstar VCF 9.1 architecture."""

from __future__ import annotations

import ast
import ipaddress
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "architecture.json"
INSTALLER = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
ARTIFACT_SCHEMA = ROOT / "schemas" / "architecture-artifact.schema.json"
REQUIREMENTS = ROOT / "fixtures" / "design_requirements.json"
INVENTORY = ROOT / "fixtures" / "estate_inventory.json"
COMPATIBILITY = ROOT / "fixtures" / "compatibility_snapshot.json"
RESEARCH = ROOT / "research.json"


class VerificationError(AssertionError):
    """A deterministic acceptance assertion failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def json_type_matches(value: Any, expected: str) -> bool:
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
    return True


def resolve_pointer(document: Any, reference: str) -> Any:
    require(reference.startswith("#/"), f"unsupported schema reference {reference!r}")
    current = document
    for raw in reference[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        require(isinstance(current, dict) and token in current, f"unresolved schema reference {reference!r}")
        current = current[token]
    return current


def validate_json_schema(
    value: Any,
    schema: Any,
    document: Any,
    path: str = "$",
) -> None:
    """Validate the JSON Schema/OpenAPI keywords used by the bundled schemas."""
    if isinstance(schema, bool):
        require(schema, f"{path}: schema rejects the value")
        return
    require(isinstance(schema, dict), f"{path}: invalid schema node")
    if value is None and schema.get("nullable") is True:
        return

    reference = schema.get("$ref")
    if reference is not None:
        validate_json_schema(value, resolve_pointer(document, reference), document, path)

    for subschema in schema.get("allOf", []):
        validate_json_schema(value, subschema, document, path)
    if "anyOf" in schema:
        matches = 0
        for subschema in schema["anyOf"]:
            try:
                validate_json_schema(value, subschema, document, path)
            except VerificationError:
                continue
            matches += 1
        require(matches >= 1, f"{path}: no anyOf branch matched")
    if "oneOf" in schema:
        matches = 0
        for subschema in schema["oneOf"]:
            try:
                validate_json_schema(value, subschema, document, path)
            except VerificationError:
                continue
            matches += 1
        require(matches == 1, f"{path}: expected exactly one matching oneOf branch, got {matches}")
    if "not" in schema:
        try:
            validate_json_schema(value, schema["not"], document, path)
        except VerificationError:
            pass
        else:
            raise VerificationError(f"{path}: value matches a forbidden schema")

    if "const" in schema:
        require(value == schema["const"], f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema:
        require(value in schema["enum"], f"{path}: value {value!r} is not in the enum")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        require(
            any(json_type_matches(value, item) for item in expected_type),
            f"{path}: expected one of types {expected_type!r}",
        )
    elif isinstance(expected_type, str):
        require(json_type_matches(value, expected_type), f"{path}: expected type {expected_type}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        require(not missing, f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                validate_json_schema(child, properties[name], document, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise VerificationError(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_json_schema(
                    child,
                    schema["additionalProperties"],
                    document,
                    f"{path}.{name}",
                )
        if "minProperties" in schema:
            require(len(value) >= schema["minProperties"], f"{path}: too few properties")
        if "maxProperties" in schema:
            require(len(value) <= schema["maxProperties"], f"{path}: too many properties")

    if isinstance(value, list):
        if "minItems" in schema:
            require(len(value) >= schema["minItems"], f"{path}: too few items")
        if "maxItems" in schema:
            require(len(value) <= schema["maxItems"], f"{path}: too many items")
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            require(len(encoded) == len(set(encoded)), f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_json_schema(item, item_schema, document, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema:
            require(len(value) >= schema["minLength"], f"{path}: string is too short")
        if "maxLength" in schema:
            require(len(value) <= schema["maxLength"], f"{path}: string is too long")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value) is not None
            except re.error as error:
                raise VerificationError(f"{path}: invalid schema pattern: {error}") from error
            require(matched, f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            require(value >= schema["minimum"], f"{path}: value is below minimum")
        if "maximum" in schema:
            require(value <= schema["maximum"], f"{path}: value is above maximum")
        if "exclusiveMinimum" in schema and not isinstance(schema["exclusiveMinimum"], bool):
            require(value > schema["exclusiveMinimum"], f"{path}: value is below exclusive minimum")
        if "exclusiveMaximum" in schema and not isinstance(schema["exclusiveMaximum"], bool):
            require(value < schema["exclusiveMaximum"], f"{path}: value is above exclusive maximum")


def exact(actual: Any, expected: Any, label: str) -> None:
    require(actual == expected, f"{label}: expected {expected!r}, got {actual!r}")


def expected_network(
    network: dict[str, Any], design: dict[str, Any]
) -> dict[str, Any]:
    defaults = design["network_defaults"]
    return {
        "networkType": network["network_type"],
        "vlanId": network["vlan_id"],
        "subnet": network["subnet"],
        "gateway": network["gateway"],
        "subnetMask": network["subnet_mask"],
        "includeIpAddressRanges": [
            {
                "startIpAddress": network["range_start"],
                "endIpAddress": network["range_end"],
            }
        ],
        "mtu": network["mtu"],
        "teamingPolicy": defaults["teaming_policy"],
        "ipAddressVersion": defaults["ip_address_version"],
        "ipAddressAssignmentMode": defaults["ip_address_assignment_mode"],
    }


def verify_sddc_semantics(sddc: dict[str, Any], requirements: dict[str, Any]) -> None:
    primary = requirements["sites"][0]
    names = requirements["primary_appliance_names"]
    password = requirements["credential_placeholder"]
    domain = requirements["dns"]["domain"]
    target = requirements["target_release"]
    design = requirements["primary_sddc_design"]
    deploy_new = design["deploy_new_components"]

    exact(sddc.get("sddcId"), primary["sddc_id"], "SddcSpec sddcId")
    exact(sddc.get("workflowType"), design["workflow_type"], "SddcSpec workflowType")
    exact(sddc.get("version"), target, "SddcSpec version")
    exact(sddc.get("vcfInstanceName"), primary["vcf_instance"], "VCF instance name")
    exact(sddc.get("ceipEnabled"), design["ceip_enabled"], "CEIP choice")
    exact(
        sddc.get("skipEsxThumbprintValidation"),
        not design["validate_esx_thumbprints"],
        "ESXi validation setting",
    )
    exact(
        sddc.get("skipGatewayPingValidation"),
        not design["validate_gateway_ping"],
        "gateway validation setting",
    )
    exact(
        sddc.get("dnsSpec"),
        {"subdomain": domain, "nameservers": requirements["dns"]["nameservers"]},
        "DNS spec",
    )
    exact(sddc.get("ntpServers"), requirements["ntp_servers"], "NTP servers")

    expected_hosts = [
        {
            "hostname": f"{primary['management_host_prefix']}{index:02d}",
            "credentials": {"username": design["esx_username"], "password": password},
        }
        for index in range(1, primary["management_domain_hosts"] + 1)
    ]
    exact(sddc.get("hostSpecs"), expected_hosts, "management host specifications")
    exact(
        sddc.get("vcenterSpec"),
        {
            "vcenterHostname": names["vcenter"],
            "rootVcenterPassword": password,
            "vmSize": design["vcenter"]["vm_size"],
            "storageSize": design["vcenter"]["storage_size"],
            "ssoDomain": design["vcenter"]["sso_domain"],
            "adminUserSsoUsername": design["vcenter"]["admin_username"],
            "adminUserSsoPassword": password,
            "version": target,
            "useExistingDeployment": not deploy_new,
        },
        "vCenter specification",
    )
    exact(
        sddc.get("clusterSpec"),
        {
            "datacenterName": design["cluster"]["datacenter_name"],
            "clusterName": design["cluster"]["cluster_name"],
            "clusterEvcMode": design["cluster"]["evc_mode"],
        },
        "management cluster specification",
    )
    exact(
        sddc.get("datastoreSpec"),
        {
            "vsanSpec": {
                "datastoreName": design["vsan"]["datastore_name"],
                "vsanDedup": design["vsan"]["dedup"],
                "failuresToTolerate": design["vsan"]["failures_to_tolerate"],
            }
        },
        "vSAN specification",
    )

    expected_networks = [
        expected_network(item, design) for item in requirements["primary_networks"]
    ]
    exact(sddc.get("networkSpecs"), expected_networks, "primary network specifications")
    exact(
        sddc.get("dvsSpecs"),
        [
            {
                "dvsName": design["distributed_switch"]["name"],
                "networks": [item["network_type"] for item in requirements["primary_networks"]],
                "mtu": design["distributed_switch"]["mtu"],
                "vmnicsToUplinks": design["distributed_switch"]["vmnics_to_uplinks"],
            }
        ],
        "distributed switch specification",
    )
    exact(
        sddc.get("nsxtSpec"),
        {
            "nsxtManagers": [{"hostname": item} for item in names["nsx_managers"]],
            "nsxtManagerSize": design["nsx_manager_size"],
            "vipFqdn": names["nsx_vip"],
            "rootNsxtManagerPassword": password,
            "nsxtAdminPassword": password,
            "nsxtAuditPassword": password,
            "transportVlanId": requirements["primary_nsx_transport_vlan"],
            "version": target,
            "useExistingDeployment": not deploy_new,
        },
        "NSX specification",
    )
    exact(
        sddc.get("sddcManagerSpec"),
        {
            "hostname": names["sddc_manager"],
            "rootPassword": password,
            "sshPassword": password,
            "localUserPassword": password,
            "version": target,
            "useExistingDeployment": not deploy_new,
        },
        "SDDC Manager specification",
    )

    management = requirements["management_services"]
    fleet_network = next(
        item for item in requirements["primary_networks"] if item["network_type"] == "FLEET_MANAGEMENT"
    )
    pool = ipaddress.ip_network(fleet_network["subnet"])
    require(
        ipaddress.ip_network(management["internal_service_cidr"]).is_private,
        "the documented internal service CIDR must be treated as a private-use design range",
    )
    require(
        not ipaddress.ip_network(management["internal_service_cidr"]).overlaps(pool),
        "internal service CIDR overlaps the management services pool",
    )
    exact(
        sddc.get("vspClusterSpec"),
        {
            "platformFqdn": management["platform_fqdn"],
            "systemUserPassword": password,
            "ipv4Pool": {
                "cidr": fleet_network["subnet"],
                "ipRange": {
                    "startIpAddress": fleet_network["range_start"],
                    "endIpAddress": fleet_network["range_end"],
                },
            },
            "size": design["management_services_size"],
            "internalClusterCidrIpv4": management["internal_service_cidr"],
            "instanceFqdn": management["instance_fqdn"],
            "fleetFqdn": management["fleet_fqdn"],
            "version": target,
            "useExistingDeployment": not deploy_new,
        },
        "VCF Management Services cluster specification",
    )
    exact(
        sddc.get("vcfManagementComponentsInfrastructureSpec"),
        {
            "localRegionNetwork": {
                "networkName": "FLEET_MANAGEMENT",
                "subnetMask": fleet_network["subnet_mask"],
                "gateway": fleet_network["gateway"],
            }
        },
        "management components infrastructure",
    )
    exact(
        sddc.get("vcfOperationsSpec"),
        {
            "nodes": [
                {"hostname": name, "rootUserPassword": password, "type": node_type}
                for name, node_type in zip(
                    names["operations_nodes"],
                    design["operations"]["node_types"],
                    strict=True,
                )
            ],
            "adminUserPassword": password,
            "applianceSize": design["operations"]["appliance_size"],
            "loadBalancerFqdn": names["operations_load_balancer"],
            "useExistingDeployment": not deploy_new,
            "version": target,
        },
        "VCF Operations specification",
    )
    exact(
        sddc.get("licenseServerSpec"),
        {
            "hostname": names["license_server"],
            "version": target,
            "useExistingDeployment": not deploy_new,
        },
        "license server specification",
    )


def required_workload_hosts(
    demand: dict[str, int],
    host: dict[str, int],
    headroom_percent: int,
    fraction_percent: int,
    failures: int,
) -> int:
    multiplier = (100 + headroom_percent) / 100 * fraction_percent / 100
    requirements = (
        math.ceil(demand["vcpus"] * multiplier / host["usable_vcpus"]),
        math.ceil(demand["memory_tib"] * multiplier / host["usable_memory_tib"]),
        math.ceil(
            demand["usable_storage_tib"] * multiplier / host["usable_storage_tib"]
        ),
    )
    return max(requirements) + failures


def verify_greenfield(artifact: dict[str, Any], requirements: dict[str, Any]) -> None:
    greenfield = artifact["greenfield"]
    exact(artifact["target_release"], requirements["target_release"], "artifact target release")
    exact(
        greenfield["deployment_model"],
        requirements["availability"]["deployment_model"],
        "two-site deployment model",
    )
    verify_sddc_semantics(greenfield["sddc_spec"], requirements)

    demand = requirements["product_demand"]
    host = requirements["workload_host_profile"]
    primary_hosts = required_workload_hosts(
        demand,
        host,
        demand["design_headroom_percent"],
        100,
        requirements["sites"][0]["host_failures_to_tolerate"],
    )
    recovery_hosts = required_workload_hosts(
        demand,
        host,
        demand["design_headroom_percent"],
        demand["recovery_capacity_percent"],
        requirements["sites"][1]["host_failures_to_tolerate"],
    )
    exact(
        greenfield["capacity_basis"],
        {
            "product_demand": {
                "vcpus": demand["vcpus"],
                "memory_tib": demand["memory_tib"],
                "usable_storage_tib": demand["usable_storage_tib"],
            },
            "design_headroom_percent": demand["design_headroom_percent"],
            "recovery_capacity_percent": demand["recovery_capacity_percent"],
            "host_profile": {
                "vcpus": host["usable_vcpus"],
                "memory_tib": host["usable_memory_tib"],
                "usable_storage_tib": host["usable_storage_tib"],
            },
            "required_hosts": {"primary": primary_hosts, "recovery": recovery_hosts},
        },
        "capacity basis",
    )

    expected_sites: list[dict[str, Any]] = []
    for site, workload_hosts in zip(
        requirements["sites"], [primary_hosts, recovery_hosts], strict=True
    ):
        failures = site["host_failures_to_tolerate"]
        surviving = workload_hosts - failures
        expected_sites.append(
            {
                "site_id": site["site_id"],
                "role": site["role"],
                "vcf_instance": site["vcf_instance"],
                "management_domain_hosts": site["management_domain_hosts"],
                "workload_domain_id": site["workload_domain_id"],
                "workload_hosts": workload_hosts,
                "resilience": f"N+{failures}",
                "surviving_workload_capacity": {
                    "vcpus": surviving * host["usable_vcpus"],
                    "memory_tib": surviving * host["usable_memory_tib"],
                    "usable_storage_tib": surviving * host["usable_storage_tib"],
                },
            }
        )
    exact(greenfield["sites"], expected_sites, "site architecture")

    availability = requirements["availability"]
    management = requirements["management_services"]
    exact(
        greenfield["availability"],
        {
            "inter_site_rtt_ms": availability["inter_site_rtt_ms"],
            "cluster_model": availability["cluster_model"],
            "rpo_minutes": availability["rpo_minutes"],
            "rto_minutes": availability["rto_minutes"],
            "management_services_ip_addresses": management["reserved_ip_addresses"],
            "management_services_minimum_ip_addresses": management["minimum_ip_addresses"],
            "internal_service_cidr": management["internal_service_cidr"],
        },
        "availability architecture",
    )
    require(
        management["reserved_ip_addresses"] >= management["minimum_ip_addresses"],
        "management services address reservation is below the published minimum",
    )


def verify_migration(
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    migration = artifact["migration"]
    exact(migration["inventory_id"], inventory["inventory_id"], "migration inventory id")
    exact(migration["target_release"], snapshot["target_release"], "migration target release")
    exact(migration["support_boundary"], snapshot["support_boundary"], "support boundary")

    components = {item["id"]: item for item in inventory["components"]}
    order = snapshot["ordered_component_ids"]
    exact(set(components), set(order), "snapshot/inventory component coverage")
    steps = migration["ordered_steps"]
    require(len(steps) == len(components), "migration must contain every component exactly once")
    exact([step["component_id"] for step in steps], order, "migration component order")
    exact([step["order"] for step in steps], list(range(1, len(steps) + 1)), "step ordinals")
    require(len({step["component_id"] for step in steps}) == len(steps), "duplicate migration component")

    for step in steps:
        component_id = step["component_id"]
        source = components[component_id]
        rule = snapshot["rules"][component_id]
        exact(step["component"], source["name"], f"{component_id} component name")
        exact(step["from_version"], source["version"], f"{component_id} source version")
        exact(
            step["target"],
            {
                "component": rule["target_component"],
                "version": rule["target_version"],
                "disposition": rule["disposition"],
            },
            f"{component_id} target",
        )
        exact(step["path"], rule["path"], f"{component_id} supported path")
        exact(step["actions"], rule["actions"], f"{component_id} actions")
        exact(step["gates"], rule["gates"], f"{component_id} technical gates")
        exact(step["path"][0], source["version"], f"{component_id} path origin")


def verify_research() -> None:
    require(RESEARCH.is_file(), "missing required research.json")
    research = load_json(RESEARCH)
    require(isinstance(research, dict), "research.json must contain an object")
    consulted = research.get("consulted")
    discrepancies = research.get("snapshot_discrepancies")
    require(isinstance(consulted, list) and consulted, "research consulted must be nonempty")
    require(
        isinstance(discrepancies, list)
        and all(isinstance(item, str) and item.strip() for item in discrepancies),
        "snapshot_discrepancies must be an array of nonempty strings",
    )

    expected_topics = {
        "compatibility",
        "product-interoperability",
        "upgrade-path",
        "lifecycle",
        "vcf-9.1-upgrade",
    }
    covered_topics: set[str] = set()
    urls: list[str] = []
    for index, source in enumerate(consulted):
        label = f"research consulted[{index}]"
        require(isinstance(source, dict), f"{label} must be an object")
        for field in ("title", "url", "retrieved_at", "finding"):
            require(
                isinstance(source.get(field), str) and source[field].strip(),
                f"{label}.{field} must be a nonempty string",
            )

        topics = source.get("topics")
        require(
            isinstance(topics, list)
            and topics
            and all(isinstance(topic, str) for topic in topics),
            f"{label}.topics must be a nonempty string array",
        )
        topic_set = set(topics)
        require(
            topic_set <= expected_topics,
            f"{label}.topics contains an unsupported topic",
        )
        covered_topics.update(topic_set)

        parsed = urlsplit(source["url"])
        hostname = (parsed.hostname or "").lower()
        official = any(
            hostname == domain or hostname.endswith("." + domain)
            for domain in ("broadcom.com", "vmware.com")
        )
        require(
            parsed.scheme == "https"
            and official
            and not parsed.username
            and not parsed.password,
            f"{label}.url must be an official Broadcom or VMware HTTPS URL",
        )
        urls.append(source["url"])

        try:
            retrieved = datetime.fromisoformat(
                source["retrieved_at"].replace("Z", "+00:00")
            )
        except ValueError as error:
            raise VerificationError(f"{label}.retrieved_at is not ISO 8601") from error
        require(
            retrieved.tzinfo is not None and retrieved.utcoffset() is not None,
            f"{label}.retrieved_at must include a UTC offset",
        )

    exact(covered_topics, expected_topics, "research topic coverage")
    require(len(urls) == len(set(urls)), "research URLs must be unique")


def verify_stdlib_package(artifact: dict[str, Any]) -> None:
    package = ROOT / "vcf_architecture"
    modules = [package / "__init__.py", package / "__main__.py"]
    require(all(path.is_file() for path in modules), "vcf_architecture package is incomplete")
    python_files = sorted(package.rglob("*.py"))
    require(python_files, "vcf_architecture package contains no Python modules")
    allowed = set(sys.stdlib_module_names) | {"vcf_architecture"}
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            raise VerificationError(f"{path.relative_to(ROOT)} has invalid syntax: {error}") from error
        for node in ast.walk(tree):
            imported: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    require(top in allowed, f"non-stdlib import {alias.name!r} in {path.relative_to(ROOT)}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = node.module.split(".", 1)[0]
            if imported is not None:
                require(imported in allowed, f"non-stdlib import {node.module!r} in {path.relative_to(ROOT)}")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as directory:
        generated = Path(directory) / "architecture.json"
        inputs = {
            "requirements": "fixtures/design_requirements.json",
            "inventory": "fixtures/estate_inventory.json",
            "compatibility": "fixtures/compatibility_snapshot.json",
        }

        def command_for(values: dict[str, str], output: Path) -> list[str]:
            return [
                sys.executable,
                "-B",
                "-m",
                "vcf_architecture",
                "--requirements",
                values["requirements"],
                "--inventory",
                values["inventory"],
                "--compatibility",
                values["compatibility"],
                "--output",
                str(output),
            ]

        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        generated.write_text('{"stale": true}\n', encoding="utf-8")
        result = subprocess.run(
            command_for(inputs, generated),
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        require(
            result.returncode == 0,
            f"package CLI failed: {(result.stdout + result.stderr).strip()[-500:]}",
        )
        require(generated.is_file(), "package CLI did not create its requested output")
        exact(load_json(generated), artifact, "deterministic package output")

        malformed = Path(directory) / "malformed.json"
        malformed.write_text('{"broken":', encoding="utf-8")
        incomplete = Path(directory) / "incomplete.json"
        incomplete.write_text("{}\n", encoding="utf-8")
        for invalid in (malformed, incomplete):
            for label in inputs:
                rejected_inputs = dict(inputs)
                rejected_inputs[label] = str(invalid)
                rejected_output = Path(directory) / f"rejected-{invalid.stem}-{label}.json"
                rejected = subprocess.run(
                    command_for(rejected_inputs, rejected_output),
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                require(
                    rejected.returncode != 0,
                    f"package CLI accepted malformed {label} input",
                )


def main() -> int:
    try:
        # The installer's own SddcSpec schema is deliberately the first validation.
        require(ARTIFACT.is_file(), "missing required architecture.json")
        artifact = load_json(ARTIFACT)
        installer = load_json(INSTALLER)
        try:
            sddc = artifact["greenfield"]["sddc_spec"]
        except (KeyError, TypeError) as error:
            raise VerificationError(
                "installer SddcSpec validation cannot begin: greenfield.sddc_spec is missing"
            ) from error
        sddc_schema = installer["components"]["schemas"]["SddcSpec"]
        validate_json_schema(sddc, sddc_schema, installer, "$.greenfield.sddc_spec")

        # Only after installer-schema success may the verifier inspect scenario authority.
        artifact_schema = load_json(ARTIFACT_SCHEMA)
        validate_json_schema(artifact, artifact_schema, artifact_schema)
        requirements = load_json(REQUIREMENTS)
        inventory = load_json(INVENTORY)
        snapshot = load_json(COMPATIBILITY)
        verify_greenfield(artifact, requirements)
        verify_migration(artifact, inventory, snapshot)
        verify_research()
        verify_stdlib_package(artifact)
    except (
        OSError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
        VerificationError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.1 SddcSpec, two-site architecture, and pinned migration plan verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
