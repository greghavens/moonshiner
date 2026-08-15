#!/usr/bin/env python3
"""Offline protected verifier for the VCF architecture seed."""

from __future__ import annotations

import ast
from datetime import date
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
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SDDC_PATH = ROOT / "architecture" / "greenfield-sddc-spec.json"
PLAN_PATH = ROOT / "architecture" / "migration-plan.json"
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
INVENTORY_PATH = ROOT / "fixtures" / "estate-inventory.json"
SNAPSHOT_PATH = ROOT / "grading" / "compatibility-snapshot.json"
PLAN_SCHEMA_PATH = ROOT / "schemas" / "migration-plan.schema.json"
RESEARCH_PATH = ROOT / "research.md"

PROTECTED_HASHES = {
    ".gitignore": "07f1dd99a91cb1349496e454099c9831c60ad88125e66d76ded8c76245d21348",
    "fixtures/estate-inventory.json": "93e40d4142cf96cb11ef417952a1ecd07530a3fed64cd79daaf4901720f35e7c",
    "grading/compatibility-snapshot.json": "7cb99fc4d5b904f5da5a46548f6487be1a22a9c89053b1241dd11e0070a5ccc5",
    "schemas/migration-plan.schema.json": "907d5a99c9662d46a8fd4eab525a6eff7df8eef9af7323c585cd724f72f8f5fb",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "specifications/vcf-installer/LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    "run_tests.sh": "09505fbc69da93f6bce097f71c1eaf33dd20bd93eeec340a9cc4cfd2a23c643e",
}


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required artifact: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: line {exc.lineno}, column {exc.colno}"
        ) from exc


def resolve_ref(document: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        raise VerificationError(f"only local schema references are supported, got {ref!r}")
    value = document
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            value = value[token]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"unresolvable schema reference {ref!r}") from exc
    return value


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
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_schema(
    value: Any,
    schema: Any,
    document: Any,
    path: str = "$",
) -> list[str]:
    """Validate the JSON Schema/OpenAPI subset used by the two pinned schemas."""

    if schema is True:
        return []
    if schema is False:
        return [f"{path}: value is forbidden by schema"]
    if not isinstance(schema, dict):
        return [f"{path}: malformed schema node"]

    errors: list[str] = []
    if "$ref" in schema:
        errors.extend(validate_schema(value, resolve_ref(document, schema["$ref"]), document, path))
        siblings = {key: item for key, item in schema.items() if key != "$ref"}
        if siblings:
            errors.extend(validate_schema(value, siblings, document, path))
        return errors

    if value is None and schema.get("nullable") is True:
        return []

    for branch in schema.get("allOf", []):
        errors.extend(validate_schema(value, branch, document, path))
    if "anyOf" in schema:
        branch_errors = [validate_schema(value, branch, document, path) for branch in schema["anyOf"]]
        if all(branch for branch in branch_errors):
            errors.append(f"{path}: value does not satisfy anyOf")
    if "oneOf" in schema:
        matches = sum(not validate_schema(value, branch, document, path) for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: value satisfies {matches} oneOf branches, expected exactly one")
    if "not" in schema and not validate_schema(value, schema["not"], document, path):
        errors.append(f"{path}: value satisfies forbidden 'not' schema")

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not in enum {schema['enum']!r}")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        type_ok = any(_json_type_matches(value, item) for item in expected_type)
    elif isinstance(expected_type, str):
        type_ok = _json_type_matches(value, expected_type)
    else:
        type_ok = True
    if not type_ok:
        errors.append(f"{path}: expected type {expected_type!r}, got {type(value).__name__}")
        return errors

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(validate_schema(item, properties[key], document, child_path))
            elif "additionalProperties" in schema:
                additional = schema["additionalProperties"]
                if additional is False:
                    errors.append(f"{path}: unexpected property {key!r}")
                elif isinstance(additional, dict):
                    errors.extend(validate_schema(item, additional, document, child_path))
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
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: array items are not unique")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, schema["items"], document, f"{path}[{index}]"))

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(f"{path}: string does not match pattern {schema['pattern']!r}")
            except re.error as exc:
                raise VerificationError(f"invalid regex in pinned schema at {path}: {exc}") from exc

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: number is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: number is above maximum {schema['maximum']}")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(exclusive_minimum, (int, float)) and not isinstance(exclusive_minimum, bool):
            if value <= exclusive_minimum:
                errors.append(f"{path}: number is not above exclusiveMinimum {exclusive_minimum}")
        elif exclusive_minimum is True and "minimum" in schema and value <= schema["minimum"]:
            errors.append(f"{path}: number is not above exclusive minimum {schema['minimum']}")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(exclusive_maximum, (int, float)) and not isinstance(exclusive_maximum, bool):
            if value >= exclusive_maximum:
                errors.append(f"{path}: number is not below exclusiveMaximum {exclusive_maximum}")
        elif exclusive_maximum is True and "maximum" in schema and value >= schema["maximum"]:
            errors.append(f"{path}: number is not below exclusive maximum {schema['maximum']}")

    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_protected_inputs() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        path = ROOT / relative
        require(path.is_file(), f"protected input is missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(actual == expected, f"protected input was modified: {relative}")


def verify_research_record(inventory: dict[str, Any]) -> None:
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationError("missing required artifact: research.md") from exc
    require(bool(text.strip()), "research.md is empty")

    url_pattern = re.compile(r"https://[^\s<>]+")
    source_lines = [
        line.strip()
        for line in text.splitlines()
        if line.lstrip().startswith("- ") and url_pattern.search(line)
    ]
    require(source_lines, "research.md must contain source bullets")
    all_urls = url_pattern.findall(text)
    require(
        len(all_urls) == len(source_lines),
        "research.md must record exactly one HTTPS URL in each source bullet",
    )
    for line, url in zip(source_lines, all_urls, strict=True):
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        official = (
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "vmware.com"
            or hostname.endswith(".vmware.com")
            or (hostname == "github.com" and parsed.path.startswith("/vmware/"))
        )
        require(official, f"research source is not Broadcom/VMware-published: {url}")
        require(
            not any(marker in hostname for marker in ("localhost", ".invalid")),
            f"research source is not a live public URL: {url}",
        )

        title = line[: line.index(url)].strip("- |*[]()—")
        require(bool(title), f"research source is missing a title: {url}")
        date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", line)
        require(date_match is not None, f"research source is missing an access date: {url}")
        try:
            date.fromisoformat(date_match.group(1))
        except ValueError as exc:
            raise VerificationError(f"research source has an invalid access date: {url}") from exc
        conclusion = line[line.index(url) + len(url) :]
        conclusion = re.sub(r"\bAccessed\s+20\d{2}-\d{2}-\d{2}\b", "", conclusion, flags=re.IGNORECASE)
        conclusion = conclusion.strip(" |*[]()—")
        require(bool(conclusion), f"research source is missing a design conclusion: {url}")

    normalized = "\n".join(source_lines).casefold()
    aliases = {
        "VMware Aria Suite Lifecycle": ("aria suite lifecycle", "lifecycle manager"),
        "VMware Aria Operations": ("aria operations", "vcf operations"),
        "VMware Aria Automation": ("aria automation", "vcf automation"),
        "VMware Aria Operations for Logs": ("operations for logs", "log management"),
        "VMware Live Site Recovery": ("live site recovery", "protection and recovery"),
        "VMware NSX": ("nsx",),
        "VMware vCenter Server": ("vcenter",),
        "VMware ESXi": ("esxi",),
        "VMware vSAN": ("vsan",),
    }
    products = {item["product"] for item in inventory["components"]}
    for product in products:
        require(
            any(alias in normalized for alias in aliases[product]),
            f"research.md does not cover inventory product {product}",
        )

    required_topics = {
        "compatibility": ("compatib",),
        "interoperability": ("interoperab",),
        "brownfield/converge path": ("brownfield", "converg", "import"),
        "upgrade order": ("sequence", "order", "preced"),
        "sizing": ("sizing", "capacity", "profile"),
    }
    for topic, terms in required_topics.items():
        require(
            any(term in normalized for term in terms),
            f"research.md does not record {topic} research",
        )


def verify_design_only_credentials(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            normalized_key = key.casefold().replace("_", "").replace("-", "")
            secret_field = any(
                marker in normalized_key
                for marker in ("password", "secret", "token", "privatekey", "apikey")
            )
            if secret_field and isinstance(item, str) and item:
                placeholder = item.casefold()
                require(
                    any(
                        marker in placeholder
                        for marker in ("design", "placeholder", "example", "sample", "changeme")
                    ),
                    f"credential at {child_path} is not an explicit design-only placeholder",
                )
            verify_design_only_credentials(item, child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            verify_design_only_credentials(item, f"{path}[{index}]")


def verify_management_components(
    components: Any,
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    management_domain: str,
    label: str,
) -> dict[str, dict[str, Any]]:
    require(isinstance(components, list), f"{label} management components must be an array")
    by_product: dict[str, dict[str, Any]] = {}
    for component in components:
        require(isinstance(component, dict), f"{label} management component must be an object")
        product = component.get("product")
        require(product not in by_product, f"{label} has duplicate management product {product!r}")
        by_product[product] = component

    rules = snapshot["managementProducts"]
    require(set(by_product) == set(rules), f"{label} must place exactly {sorted(rules)}")
    demand = inventory["designRequirements"]["serviceDemand"]

    total_vcpu = 0
    total_memory = 0
    total_storage = 0.0
    for product, rule in rules.items():
        component = by_product[product]
        profile_name = component.get("profile")
        require(profile_name in rule["profiles"], f"{label} selected unknown profile {profile_name!r} for {product}")
        profile = rule["profiles"][profile_name]
        require(component.get("version") == rule["targetVersion"], f"{label} has wrong {product} version")
        require(component.get("placementDomain") == management_domain, f"{label} must place {product} in management domain")
        for key in (
            "nodeCount",
            "vcpuPerNode",
            "memoryGiBPerNode",
            "storageTiBTotal",
            "capacity",
        ):
            require(component.get(key) == profile[key], f"{label} {product} {key} must match pinned profile")
        metric = rule["demandMetric"]
        required_demand = demand[metric]
        require(component.get("demand") == required_demand, f"{label} {product} must state fixture demand")
        require(profile["capacity"] >= required_demand, f"{label} {product} profile is undersized")
        if "retentionMetric" in rule:
            required_retention = demand[rule["retentionMetric"]]
            require(component.get("retentionDays") == profile["retentionDays"], f"{label} {product} retention must match profile")
            require(profile["retentionDays"] >= required_retention, f"{label} {product} retention is undersized")
        total_vcpu += profile["nodeCount"] * profile["vcpuPerNode"]
        total_memory += profile["nodeCount"] * profile["memoryGiBPerNode"]
        total_storage += profile["storageTiBTotal"]

    available = inventory["designRequirements"]["managementDomain"]["availableCapacity"]
    require(total_vcpu <= available["vcpu"], f"{label} management products exceed available vCPU")
    require(total_memory <= available["memoryGiB"], f"{label} management products exceed available memory")
    require(total_storage <= available["storageTiB"], f"{label} management products exceed available storage")
    return by_product


def verify_greenfield(
    sddc: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    requirements = inventory["designRequirements"]
    mgmt = requirements["managementDomain"]
    workload = requirements["workloadDomains"]
    target = snapshot["targetFleetVersion"]

    require(sddc.get("sddcId") == mgmt["id"], "SddcSpec sddcId must identify the management domain")
    require(sddc.get("workflowType") == "VCF", "greenfield SddcSpec workflowType must be VCF")
    require(sddc.get("version") == target, "greenfield SddcSpec must target the pinned fleet version")
    require(len(sddc.get("hostSpecs", [])) == mgmt["hostCount"], "management host count does not match fixture")
    require(len({host.get("hostname") for host in sddc["hostSpecs"]}) == mgmt["hostCount"], "management hostnames must be unique")
    require(sddc.get("vcenterSpec", {}).get("version") == target, "greenfield vCenter version is wrong")
    require(sddc.get("nsxtSpec", {}).get("version") == target, "greenfield NSX version is wrong")
    require(sddc.get("sddcManagerSpec", {}).get("version") == target, "greenfield SDDC Manager version is wrong")
    require(sddc.get("dnsSpec") == requirements["dns"], "greenfield DNS design must match fixture")
    require(sddc.get("ntpServers") == requirements["ntpServers"], "greenfield NTP design must match fixture")
    verify_design_only_credentials(sddc)

    expected_networks = {
        (item["type"], item["vlanId"], item["subnet"], item["gateway"])
        for item in requirements["networks"]
    }
    actual_networks = {
        (item.get("networkType"), item.get("vlanId"), item.get("subnet"), item.get("gateway"))
        for item in sddc.get("networkSpecs", [])
    }
    require(actual_networks == expected_networks, "greenfield networks must match fixture types, VLANs, subnets, and gateways")

    extension = sddc.get("x-vcf-architecture")
    require(isinstance(extension, dict), "SddcSpec must contain x-vcf-architecture")
    fleet = extension.get("fleet", {})
    require(fleet.get("fleetId") == requirements["fleetId"], "greenfield extension has wrong fleetId")
    require(fleet.get("targetVersion") == target, "greenfield extension has wrong fleet target")

    domains = extension.get("domains")
    require(isinstance(domains, list), "greenfield extension domains must be an array")
    domain_by_id = {item.get("id"): item for item in domains if isinstance(item, dict)}
    expected_ids = {mgmt["id"], *(item["id"] for item in workload)}
    require(set(domain_by_id) == expected_ids, "greenfield design must contain the management and workload domains")
    require(domain_by_id[mgmt["id"]].get("role") == "management", "management domain role is wrong")
    require(domain_by_id[mgmt["id"]].get("hostCount") == mgmt["hostCount"], "extension management host count is wrong")
    for item in workload:
        domain = domain_by_id[item["id"]]
        require(domain.get("role") == "workload", f"{item['id']} must be a workload domain")
        require(domain.get("hostCount") == item["hostCount"], f"{item['id']} host count is wrong")
        stack = domain.get("targetComponents", {})
        for product in ("vCenter", "ESXi", "vSAN", "NSX"):
            require(stack.get(product) == target, f"{item['id']} must target {product} {target}")

    management = verify_management_components(
        extension.get("managementComponents"), inventory, snapshot, mgmt["id"], "greenfield"
    )
    native_ops = sddc.get("vcfOperationsSpec", {})
    ops = management["VCF Operations"]
    require(native_ops.get("version") == ops["version"], "native VCF Operations version disagrees with extension")
    require(len(native_ops.get("nodes", [])) == ops["nodeCount"], "native VCF Operations node count disagrees with extension")
    require(native_ops.get("applianceSize") == ops["profile"].split("-")[0], "native VCF Operations size disagrees with extension")
    native_automation = sddc.get("vcfAutomationSpec", {})
    automation = management["VCF Automation"]
    require(native_automation.get("version") == automation["version"], "native VCF Automation version disagrees with extension")
    require(native_automation.get("size") == automation["profile"], "native VCF Automation size disagrees with extension")
    return management


def verify_migration(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    greenfield_management: dict[str, dict[str, Any]],
) -> None:
    requirements = inventory["designRequirements"]
    target = snapshot["targetFleetVersion"]
    fleet = plan["fleet"]
    require(plan["estateId"] == inventory["estateId"], "migration plan estateId is wrong")
    require(fleet["fleetId"] == requirements["fleetId"], "migration plan fleetId is wrong")
    require(fleet["targetVersion"] == target, "migration plan fleet target is wrong")
    require(fleet["managementDomain"] == requirements["managementDomain"]["id"], "migration management domain is wrong")
    require(fleet["workloadDomains"] == [item["id"] for item in requirements["workloadDomains"]], "migration workload domains are wrong")

    plan_management = verify_management_components(
        plan["managementComponents"], inventory, snapshot, requirements["managementDomain"]["id"], "migration"
    )
    require(plan_management == greenfield_management, "greenfield and migration management-component designs must agree")

    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    targets = plan["componentTargets"]
    target_by_id = {item.get("componentId"): item for item in targets}
    require(len(target_by_id) == len(targets), "migration plan contains duplicate component targets")
    require(set(target_by_id) == set(inventory_by_id), "migration plan must name every and only inventory component")

    for component_id, source in inventory_by_id.items():
        item = target_by_id[component_id]
        rule = snapshot["componentRules"][component_id]
        require(item["name"] == source["name"], f"{component_id} name does not match inventory")
        require(item["product"] == source["product"], f"{component_id} product does not match inventory")
        require(item["sourceVersion"] == source["version"], f"{component_id} source version does not match inventory")
        require(item["targetVersion"] == rule["targetVersion"], f"{component_id} target version violates snapshot")
        require(item["disposition"] == rule["disposition"], f"{component_id} disposition violates snapshot")
        expected_domain = snapshot["domainTargets"][source["currentDomain"]]
        require(item["targetDomain"] == expected_domain, f"{component_id} target domain is wrong")
        gate_ids = [gate["id"] for gate in item["gates"]]
        require(len(gate_ids) == len(set(gate_ids)), f"{component_id} has duplicate gates")
        require(set(rule["requiredGates"]).issubset(gate_ids), f"{component_id} is missing required technical gates")

    steps = plan["steps"]
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "migration step order must be contiguous and array-ordered")
    step_by_id = {step["id"]: step for step in steps}
    require(len(step_by_id) == len(steps), "migration step IDs must be unique")
    order_by_component: dict[str, int] = {}
    seen_components: list[str] = []
    for step in steps:
        for dependency in step["dependsOn"]:
            require(dependency in step_by_id, f"step {step['id']} has unknown dependency {dependency}")
            require(step_by_id[dependency]["order"] < step["order"], f"step {step['id']} dependency must be earlier")
        for component_id in step["componentIds"]:
            require(component_id in inventory_by_id, f"step {step['id']} references unknown component {component_id}")
            seen_components.append(component_id)
            order_by_component[component_id] = step["order"]
            rule = snapshot["componentRules"][component_id]
            require(step["action"] == rule["method"], f"step {step['id']} uses wrong action for {component_id}")
            require(set(rule["requiredGates"]).issubset(step["gateIds"]), f"step {step['id']} omits gates for {component_id}")
    require(len(seen_components) == len(set(seen_components)), "each inventory component must occur in exactly one migration step")
    require(set(seen_components) == set(inventory_by_id), "migration steps must cover every inventory component")
    for before, after in snapshot["precedence"]:
        require(order_by_component[before] < order_by_component[after], f"compatibility order requires {before} before {after}")

    logs_rule = snapshot["componentRules"]["aria-logs-01"]
    retention = requirements["serviceDemand"]["logRetentionDays"]
    require(retention <= logs_rule["maximumMigratableDays"], "requested log migration window exceeds pinned path")


def verify_stdlib_package_and_regeneration() -> None:
    package = ROOT / "vcf_architecture"
    require((package / "__init__.py").is_file(), "vcf_architecture/__init__.py is missing")
    require((package / "__main__.py").is_file(), "vcf_architecture/__main__.py is missing")
    python_files = sorted(package.rglob("*.py"))
    require(python_files, "vcf_architecture package has no Python files")
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise VerificationError(f"syntax error in {path.relative_to(ROOT)}: {exc}") from exc
        for node in ast.walk(tree):
            root: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    require(root in stdlib or root == "vcf_architecture", f"third-party import {alias.name!r} in {path.relative_to(ROOT)}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                root = node.module.split(".")[0]
                require(root in stdlib or root == "vcf_architecture", f"third-party import {node.module!r} in {path.relative_to(ROOT)}")

    with tempfile.TemporaryDirectory(prefix="vcf-architecture-verify-") as temp:
        output = Path(temp) / "architecture"
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [
            sys.executable,
            "-S",
            "-m",
            "vcf_architecture",
            "--inventory",
            "fixtures/estate-inventory.json",
            "--compatibility",
            "grading/compatibility-snapshot.json",
            "--output",
            str(output),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        require(
            completed.returncode == 0,
            "package CLI failed during isolated regeneration:\n" + completed.stdout + completed.stderr,
        )
        produced = sorted(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file())
        require(
            produced == ["greenfield-sddc-spec.json", "migration-plan.json"],
            f"package CLI must write exactly the two deliverables, wrote {produced}",
        )
        require(load_json(output / "greenfield-sddc-spec.json") == load_json(SDDC_PATH), "regenerated SddcSpec differs from committed artifact")
        require(load_json(output / "migration-plan.json") == load_json(PLAN_PATH), "regenerated migration plan differs from committed artifact")


def write_result(checks: int) -> None:
    run_dir = ROOT / ".run"
    run_dir.mkdir(exist_ok=True)
    result = {
        "status": "pass",
        "checks": checks,
        "offline": True,
        "researchInspected": True,
    }
    (run_dir / "verify-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    checks = 0
    try:
        # Required first gate: validate the submitted artifact against the installer
        # specification's own SddcSpec before integrity, migration, or package checks.
        openapi = load_json(OPENAPI_PATH)
        sddc = load_json(SDDC_PATH)
        sddc_schema = resolve_ref(openapi, "#/components/schemas/SddcSpec")
        schema_errors = validate_schema(sddc, sddc_schema, openapi)
        require(not schema_errors, "SddcSpec OpenAPI validation failed:\n" + "\n".join(schema_errors[:50]))
        checks += 1

        verify_protected_inputs()
        checks += len(PROTECTED_HASHES)
        inventory = load_json(INVENTORY_PATH)
        snapshot = load_json(SNAPSHOT_PATH)
        verify_research_record(inventory)
        checks += 1
        plan_schema = load_json(PLAN_SCHEMA_PATH)
        plan = load_json(PLAN_PATH)
        plan_errors = validate_schema(plan, plan_schema, plan_schema)
        require(not plan_errors, "migration-plan schema validation failed:\n" + "\n".join(plan_errors[:50]))
        checks += 1

        greenfield_management = verify_greenfield(sddc, inventory, snapshot)
        checks += 1
        verify_migration(plan, inventory, snapshot, greenfield_management)
        checks += 1
        verify_stdlib_package_and_regeneration()
        checks += 1
        write_result(checks)
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: {checks} verification groups passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
