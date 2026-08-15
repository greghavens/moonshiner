#!/usr/bin/env python3
"""Protected, offline acceptance verifier for the VCF architecture artifacts."""

from __future__ import annotations

import ast
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
SDDC_PATH = ROOT / "artifacts/greenfield-sddc-spec.json"
SPEC_SHA256 = "a2084a65aab0ac0a5a1625d1a2fdf20b55fc8895ca43fd4389da901d07a4aaef"


class ContractError(AssertionError):
    """A deterministic architecture-contract failure."""


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError as error:
        raise ContractError(f"required artifact is missing: {path.relative_to(ROOT)}") from error
    except json.JSONDecodeError as error:
        raise ContractError(
            f"invalid JSON in {path.relative_to(ROOT)} at line {error.lineno}: {error.msg}"
        ) from error


def _json_type_matches(value: Any, expected: str) -> bool:
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


def _pointer(document: Any, reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ContractError(f"unsupported non-local schema reference: {reference}")
    value = document
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        try:
            value = value[token]
        except (KeyError, TypeError) as error:
            raise ContractError(f"unresolvable schema reference: {reference}") from error
    return value


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the JSON Schema/OpenAPI keywords used by the pinned contracts."""
    errors: list[str] = []

    if "$ref" in schema:
        errors.extend(validate_schema(value, _pointer(document, schema["$ref"]), document, path))
        schema = {key: item for key, item in schema.items() if key != "$ref"}
        if not schema:
            return errors

    if value is None and schema.get("nullable") is True:
        return errors

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type:
        expected_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(_json_type_matches(value, candidate) for candidate in expected_types):
            errors.append(f"{path}: expected type {expected_type!r}, got {type(value).__name__}")
            return errors

    for branch in schema.get("allOf", []):
        errors.extend(validate_schema(value, branch, document, path))
    if "anyOf" in schema:
        branch_errors = [validate_schema(value, branch, document, path) for branch in schema["anyOf"]]
        if all(branch for branch in branch_errors):
            errors.append(f"{path}: did not satisfy anyOf")
    if "oneOf" in schema:
        matches = sum(
            not validate_schema(value, branch, document, path) for branch in schema["oneOf"]
        )
        if matches != 1:
            errors.append(f"{path}: satisfied {matches} oneOf branches, expected exactly one")
    if "not" in schema and not validate_schema(value, schema["not"], document, path):
        errors.append(f"{path}: matched a prohibited schema")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, item in value.items():
            child_path = f"{path}.{name}"
            if name in properties:
                errors.extend(validate_schema(item, properties[name], document, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    validate_schema(item, schema["additionalProperties"], document, child_path)
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
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, item_schema, document, f"{path}[{index}]"))

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than maxLength")
        pattern = schema.get("pattern")
        if pattern and re.search(pattern, value) is None:
            errors.append(f"{path}: string does not match pattern {pattern!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: value is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: value is not below exclusiveMaximum")

    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ContractError(f"{label}: expected {expected!r}, got {actual!r}")


def verify_credential_placeholders(sddc: dict[str, Any]) -> None:
    password_values: list[tuple[str, Any]] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child_path = f"{path}.{key}"
                if "password" in key.casefold():
                    password_values.append((child_path, item))
                visit(item, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(sddc, "$")
    require(password_values, "SddcSpec must contain deploy-time credential placeholders")
    for path, value in password_values:
        require(
            isinstance(value, str) and re.fullmatch(r"\$\{SECRET_[A-Z0-9_]+\}", value) is not None,
            f"{path} must be an obvious ${{SECRET_NAME}} placeholder",
        )


def verify_research(research: dict[str, Any]) -> None:
    researched_at = research.get("researchedAt")
    require(isinstance(researched_at, str), "research researchedAt must be an ISO 8601 string")
    try:
        timestamp = datetime.fromisoformat(researched_at)
    except ValueError as error:
        raise ContractError("research researchedAt must be valid ISO 8601") from error
    require(
        timestamp.tzinfo is not None and timestamp.utcoffset() is not None,
        "research researchedAt must include a UTC offset",
    )

    sources = research.get("sources")
    require(isinstance(sources, list) and bool(sources), "research sources must be a nonempty array")
    allowed_types = {"compatibility", "interoperability", "upgrade-path", "upgrade-order"}
    covered_types: set[str] = set()
    urls: set[str] = set()
    for index, source in enumerate(sources):
        require(isinstance(source, dict), f"research source {index} must be an object")
        publisher = source.get("publisher")
        require(
            isinstance(publisher, str) and "broadcom" in publisher.casefold(),
            f"research source {index} publisher must identify Broadcom",
        )
        require(
            isinstance(source.get("title"), str) and bool(source["title"].strip()),
            f"research source {index} needs a title",
        )
        url = source.get("url")
        require(isinstance(url, str), f"research source {index} needs a URL")
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").casefold()
        require(
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and (hostname == "broadcom.com" or hostname.endswith(".broadcom.com")),
            f"research source {index} must use a Broadcom-published HTTPS URL",
        )
        require(url not in urls, f"duplicate research URL {url!r}")
        urls.add(url)
        used_for = source.get("usedFor")
        require(
            isinstance(used_for, list)
            and bool(used_for)
            and all(isinstance(decision, str) and decision.strip() for decision in used_for),
            f"research source {index} needs nonempty usedFor decisions",
        )
        source_types = source.get("sourceTypes")
        require(
            isinstance(source_types, list)
            and bool(source_types)
            and all(item in allowed_types for item in source_types),
            f"research source {index} has invalid sourceTypes",
        )
        covered_types.update(source_types)
    require_equal(covered_types, allowed_types, "research source-type coverage")


def verify_sddc_design(sddc: dict[str, Any], requirements: dict[str, Any]) -> None:
    primary = next(site for site in requirements["sites"] if site["role"] == "PRIMARY")
    appliances = requirements["managementAppliances"]

    require_equal(sddc.get("sddcId"), requirements["sddcId"], "SddcSpec.sddcId")
    require_equal(sddc.get("version"), requirements["targetVersion"], "SddcSpec.version")
    require_equal(sddc.get("workflowType"), "VCF", "SddcSpec.workflowType")
    require_equal(
        sddc.get("vcfInstanceName"), requirements["vcfInstanceName"], "SddcSpec.vcfInstanceName"
    )

    hosts = sddc.get("hostSpecs", [])
    require_equal(len(hosts), primary["managementDomainHostCount"], "primary host count")
    require_equal(
        [host.get("hostname") for host in hosts], primary["hostnames"], "primary host inventory"
    )

    dns = sddc.get("dnsSpec", {})
    require_equal(dns.get("subdomain"), requirements["dns"]["subdomain"], "DNS subdomain")
    require_equal(dns.get("nameservers"), requirements["dns"]["nameservers"], "DNS servers")
    require_equal(sddc.get("ntpServers"), requirements["dns"]["ntpServers"], "NTP servers")

    vcenter = sddc.get("vcenterSpec", {})
    require_equal(vcenter.get("vcenterHostname"), appliances["vcenterHostname"], "vCenter hostname")
    require(vcenter.get("useExistingDeployment") is not True, "greenfield vCenter cannot be existing")
    manager = sddc.get("sddcManagerSpec", {})
    require_equal(manager.get("hostname"), appliances["sddcManagerHostname"], "SDDC Manager")
    require(manager.get("useExistingDeployment") is not True, "greenfield SDDC Manager cannot be existing")

    nsx = sddc.get("nsxtSpec", {})
    require_equal(nsx.get("vipFqdn"), appliances["nsxVipFqdn"], "NSX VIP")
    require_equal(
        [node.get("hostname") for node in nsx.get("nsxtManagers", [])],
        appliances["nsxManagerHostnames"],
        "NSX manager nodes",
    )
    require(nsx.get("useExistingDeployment") is not True, "greenfield NSX cannot be existing")
    fleet = sddc.get("vcfOperationsFleetManagementSpec", {})
    require_equal(fleet.get("hostname"), appliances["fleetManagementHostname"], "Fleet Manager")
    require(fleet.get("useExistingDeployment") is not True, "greenfield Fleet Manager cannot be existing")

    expected_networks = {item["networkType"]: item for item in primary["networks"]}
    actual_networks = {item.get("networkType"): item for item in sddc.get("networkSpecs", [])}
    require_equal(set(actual_networks), set(expected_networks), "SddcSpec network types")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        require_equal(actual.get("vlanId"), expected["vlanId"], f"{network_type} VLAN")
        require_equal(actual.get("subnet"), expected["cidr"], f"{network_type} CIDR")
        require_equal(actual.get("gateway"), expected["gateway"], f"{network_type} gateway")
        require_equal(actual.get("mtu"), expected["mtu"], f"{network_type} MTU")
        require_equal(
            actual.get("includeIpAddressRanges"),
            [{"startIpAddress": expected["ipRange"][0], "endIpAddress": expected["ipRange"][1]}],
            f"{network_type} IP range",
        )

    dvs_specs = sddc.get("dvsSpecs", [])
    require(dvs_specs, "SddcSpec must define its distributed switch architecture")
    covered = {name for dvs in dvs_specs for name in dvs.get("networks", [])}
    require_equal(covered, set(expected_networks), "distributed-switch network coverage")
    for dvs in dvs_specs:
        mappings = dvs.get("vmnicsToUplinks", [])
        require(len(mappings) >= 2, "each distributed switch needs at least two physical uplinks")
        require(len({item.get("id") for item in mappings}) == len(mappings), "duplicate vmnic mapping")
        require(
            len({item.get("uplink") for item in mappings}) == len(mappings),
            "duplicate distributed-switch uplink mapping",
        )

    storage = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    expected_storage = requirements["storagePolicy"]
    require_equal(storage.get("datastoreName"), expected_storage["datastoreName"], "vSAN name")
    require_equal(storage.get("failuresToTolerate"), expected_storage["failuresToTolerate"], "vSAN FTT")
    require(
        storage.get("esaConfig", {}).get("enabled") is True,
        "storage requirement calls for vSAN ESA",
    )
    require(sddc.get("skipEsxThumbprintValidation") is not True, "ESX validation cannot be skipped")
    require(sddc.get("skipGatewayPingValidation") is not True, "gateway validation cannot be skipped")


def verify_site_architecture(site_design: dict[str, Any], requirements: dict[str, Any]) -> None:
    require_equal(site_design.get("schemaVersion"), "1.0", "site schemaVersion")
    require_equal(site_design.get("requirementsId"), requirements["requirementsId"], "requirements link")
    require_equal(site_design.get("targetVersion"), requirements["targetVersion"], "site target")
    actual_sites = {site.get("siteId"): site for site in site_design.get("sites", [])}
    expected_sites = {site["siteId"]: site for site in requirements["sites"]}
    require_equal(set(actual_sites), set(expected_sites), "site coverage")

    dimensions = ("cpuCores", "memoryGiB", "rawStorageTiB")
    for site_id, expected in expected_sites.items():
        actual = actual_sites[site_id]
        host_count = expected["managementDomainHostCount"]
        survivors = host_count - expected["hostFailureTolerance"]
        require_equal(actual.get("role"), expected["role"], f"{site_id} role")
        require_equal(actual.get("hostCount"), host_count, f"{site_id} host count")
        require_equal(
            actual.get("hostFailureTolerance"),
            expected["hostFailureTolerance"],
            f"{site_id} host failure tolerance",
        )
        require_equal(actual.get("hostProfile"), expected["hostProfile"], f"{site_id} host profile")
        raw_expected = {key: expected["hostProfile"][key] * host_count for key in dimensions}
        usable_expected = {key: expected["hostProfile"][key] * survivors for key in dimensions}
        require_equal(actual.get("rawCapacity"), raw_expected, f"{site_id} raw capacity")
        require_equal(
            actual.get("usableAfterHostFailures"), usable_expected, f"{site_id} usable capacity"
        )
        require_equal(
            actual.get("minimumUsableAfterFailures"),
            expected["minimumUsableAfterFailures"],
            f"{site_id} capacity requirement",
        )
        for key in dimensions:
            require(
                usable_expected[key] >= expected["minimumUsableAfterFailures"][key],
                f"{site_id} does not meet {key} after host failures",
            )
        require_equal(actual.get("networks"), expected["networks"], f"{site_id} networks")

    require_equal(site_design.get("recovery"), requirements["recovery"], "recovery objectives")


def verify_migration(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    migration_schema: dict[str, Any],
) -> None:
    schema_errors = validate_schema(plan, migration_schema, migration_schema)
    if schema_errors:
        raise ContractError("migration schema validation failed:\n" + "\n".join(schema_errors[:20]))

    require_equal(plan["estateId"], inventory["estateId"], "migration estate")
    require_equal(plan["targetPlatform"], snapshot["targetPlatform"], "migration platform")
    require_equal(plan["targetVersion"], snapshot["targetVersion"], "migration target")
    require_equal(
        plan["compatibilitySnapshotId"], snapshot["snapshotId"], "compatibility snapshot link"
    )

    inventory_by_id = {item["componentId"]: item for item in inventory["components"]}
    rules_by_id = {item["componentId"]: item for item in snapshot["componentRules"]}
    steps = plan["steps"]
    require_equal([step["order"] for step in steps], list(range(1, len(steps) + 1)), "step order")
    require_equal(
        {step["componentId"] for step in steps}, set(inventory_by_id), "migration component coverage"
    )
    require_equal(len(steps), len(inventory_by_id), "one migration step per component")
    require_equal(set(rules_by_id), set(inventory_by_id), "snapshot component coverage")

    gate_catalog = {item["gateId"]: item["condition"] for item in snapshot["gateCatalog"]}
    order_by_id = {step["componentId"]: step["order"] for step in steps}
    for step in steps:
        component_id = step["componentId"]
        source = inventory_by_id[component_id]
        rule = rules_by_id[component_id]
        require_equal(step["componentName"], source["componentName"], f"{component_id} name")
        require_equal(step["currentVersion"], source["version"], f"{component_id} current version")
        require_equal(rule["source"], {
            "componentName": source["componentName"], "version": source["version"]
        }, f"{component_id} snapshot source")
        require_equal(step["target"], rule["target"], f"{component_id} target")
        require_equal(step["path"], rule["allowedPath"], f"{component_id} supported path")
        require_equal(step["dependsOn"], rule["dependsOn"], f"{component_id} dependencies")
        expected_gates = [
            {"gateId": gate_id, "condition": gate_catalog[gate_id]}
            for gate_id in rule["requiredGateIds"]
        ]
        require_equal(step["gates"], expected_gates, f"{component_id} compatibility gates")
        for dependency in step["dependsOn"]:
            require(
                order_by_id[dependency] < step["order"],
                f"{component_id} is ordered before dependency {dependency}",
            )

    boundary = rules_by_id["aria-suite-lifecycle"]["supportBoundary"]
    require(boundary["inPlaceUpgradeToTarget"] is False, "pinned support boundary was lost")
    require(boundary["replacementRequired"] is True, "pinned replacement requirement was lost")
    require_equal(
        next(step for step in steps if step["componentId"] == "aria-suite-lifecycle")["target"][
            "disposition"
        ],
        "replace",
        "Aria Suite Lifecycle disposition",
    )


def verify_stdlib_only() -> None:
    package = ROOT / "src/vcf_architecture"
    sources = sorted(package.rglob("*.py"))
    require(bool(sources), "vcf_architecture package is missing")
    stdlib = set(sys.stdlib_module_names)
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for root in roots:
                require(
                    root in stdlib or root == "vcf_architecture",
                    f"non-stdlib import {root!r} in {path.relative_to(ROOT)}",
                )


def run_builder(output: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-m", "vcf_architecture", "--output", str(output)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
    )
    if result.returncode != 0:
        evidence = (result.stdout + "\n" + result.stderr).strip()[-1200:]
        raise ContractError(f"architecture builder failed:\n{evidence}")


def verify_generated_matches_committed() -> None:
    graded_names = (
        "greenfield-sddc-spec.json",
        "site-architecture.json",
        "migration-plan.json",
    )
    with tempfile.TemporaryDirectory(prefix=".vcf-verify-", dir=ROOT) as first_name:
        with tempfile.TemporaryDirectory(prefix=".vcf-verify-", dir=ROOT) as second_name:
            first = Path(first_name)
            second = Path(second_name)
            run_builder(first)
            run_builder(second)
            for name in graded_names:
                committed = load_json(ROOT / "artifacts" / name)
                generated_first = load_json(first / name)
                generated_second = load_json(second / name)
                require_equal(generated_first, committed, f"generated {name}")
                require_equal(generated_second, generated_first, f"deterministic {name}")


def main() -> int:
    # Phase 1 is intentionally first: trust and apply the installer schema before
    # loading scenario fixtures, importing candidate code, or checking other output.
    spec_bytes = SPEC_PATH.read_bytes()
    require_equal(hashlib.sha256(spec_bytes).hexdigest(), SPEC_SHA256, "installer spec digest")
    installer_spec = json.loads(spec_bytes)
    sddc = load_json(SDDC_PATH)
    sddc_schema = installer_spec["components"]["schemas"]["SddcSpec"]
    schema_errors = validate_schema(sddc, sddc_schema, installer_spec)
    if schema_errors:
        raise ContractError("SddcSpec installer-schema validation failed:\n" + "\n".join(schema_errors[:20]))
    print("PASS installer SddcSpec schema")
    verify_credential_placeholders(sddc)

    requirements = load_json(ROOT / "fixtures/design-requirements.json")
    inventory = load_json(ROOT / "fixtures/estate-inventory.json")
    snapshot = load_json(ROOT / "fixtures/compatibility-snapshot.json")
    migration_schema = load_json(ROOT / "schemas/migration-plan.schema.json")
    site_design = load_json(ROOT / "artifacts/site-architecture.json")
    migration_plan = load_json(ROOT / "artifacts/migration-plan.json")
    research = load_json(ROOT / "artifacts/research-sources.json")

    verify_sddc_design(sddc, requirements)
    verify_site_architecture(site_design, requirements)
    verify_migration(migration_plan, inventory, snapshot, migration_schema)
    verify_research(research)
    verify_stdlib_only()
    verify_generated_matches_committed()
    print("PASS capacity, availability, sites, migration, and deterministic package output")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
