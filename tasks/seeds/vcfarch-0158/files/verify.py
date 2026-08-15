#!/usr/bin/env python3
"""Deterministic offline verifier for the VCF migration architecture."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing JSON artifact: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.name}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


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
    raise VerificationError(f"unsupported schema type in protected installer spec: {expected}")


def resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    require(reference.startswith("#/"), f"unsupported non-local schema reference: {reference}")
    node: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        require(isinstance(node, dict) and part in node, f"unresolved schema reference: {reference}")
        node = node[part]
    require(isinstance(node, dict), f"schema reference is not an object: {reference}")
    return node


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_schema(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    if "const" in schema:
        require(value == schema["const"], f"schema {path}: expected constant {schema['const']!r}")
    if "enum" in schema:
        require(value in schema["enum"], f"schema {path}: {value!r} is not in the allowed enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        if isinstance(expected_type, list):
            require(
                any(json_type_matches(value, item) for item in expected_type),
                f"schema {path}: unexpected value type",
            )
        else:
            require(
                json_type_matches(value, expected_type),
                f"schema {path}: expected {expected_type}, got {type(value).__name__}",
            )

    if isinstance(value, str):
        if "minLength" in schema:
            require(len(value) >= schema["minLength"], f"schema {path}: string is too short")
        if "maxLength" in schema:
            require(len(value) <= schema["maxLength"], f"schema {path}: string is too long")
        if "pattern" in schema:
            require(re.search(schema["pattern"], value) is not None, f"schema {path}: pattern mismatch")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            require(value >= schema["minimum"], f"schema {path}: below minimum")
        if "maximum" in schema:
            require(value <= schema["maximum"], f"schema {path}: above maximum")

    if isinstance(value, list):
        if "minItems" in schema:
            require(len(value) >= schema["minItems"], f"schema {path}: too few array items")
        if "maxItems" in schema:
            require(len(value) <= schema["maxItems"], f"schema {path}: too many array items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            require(len(encoded) == len(set(encoded)), f"schema {path}: duplicate array items")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_schema(item, schema["items"], root_schema, f"{path}[{index}]")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        require(not missing, f"schema {path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for name, item in value.items():
            item_path = f"{path}.{name}"
            if name in properties:
                validate_schema(item, properties[name], root_schema, item_path)
            else:
                additional = schema.get("additionalProperties", True)
                require(additional is not False, f"schema {path}: unexpected property {name!r}")
                if isinstance(additional, dict):
                    validate_schema(item, additional, root_schema, item_path)


def index_unique(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    values = [item[key] for item in items]
    require(len(values) == len(set(values)), f"duplicate {label}: {values}")
    return {item[key]: item for item in items}


def expected_entitlement(snapshot: dict[str, Any]) -> dict[str, Any]:
    rule = snapshot["entitlement_rule"]
    return {
        "source_entitlement": rule["source_entitlement"],
        "target_selection": rule["required_selection"],
        "selected_topology": rule["required_topology"],
        "excluded_topology": rule["excluded_topology"],
        "reason": rule["reason"],
    }


def content_rule_map(product_rule: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return index_unique(product_rule["content_rules"], "kind", "content rule kind")


def profile_fits(
    component_id: str,
    profile: dict[str, Any],
    inventory: dict[str, Any],
) -> bool:
    requirements = inventory["service_requirements"]
    if component_id in {"tgt-fleet", "tgt-identity"}:
        return True
    if profile["availability"] != requirements["availability"]:
        return False
    capacity = profile["capacity"]
    if component_id == "tgt-ops":
        needed = requirements["operations"]
        return all(capacity.get(key, -1) >= value for key, value in needed.items())
    if component_id == "tgt-automation":
        needed = requirements["automation"]
        return all(capacity.get(key, -1) >= value for key, value in needed.items())
    if component_id == "tgt-logs":
        needed = requirements["logs"]
        mapping = {
            "ingestion_gb_per_day": "ingestion_gb_per_day",
            "events_per_second": "events_per_second",
            "active_syslog_connections": "active_syslog_connections",
            "target_retention_days": "retention_days",
        }
        return all(capacity.get(mapping[key], -1) >= value for key, value in needed.items())
    raise VerificationError(f"unknown target component constraint: {component_id}")


def select_minimum_profile(
    constraint: dict[str, Any], inventory: dict[str, Any]
) -> dict[str, Any]:
    profiles = sorted(constraint["profiles"], key=lambda item: item["rank"])
    fitting = [item for item in profiles if profile_fits(constraint["component_id"], item, inventory)]
    require(fitting, f"protected snapshot has no fitting profile for {constraint['component_id']}")
    return fitting[0]


def expected_profile_shape(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": profile["name"],
        "availability": profile["availability"],
        "node_count": profile["node_count"],
        "vcpu_per_node": profile["vcpu_per_node"],
        "memory_gb_per_node": profile["memory_gb_per_node"],
        "disk_gb_per_node": profile["disk_gb_per_node"],
        "capacity": profile["capacity"],
    }


def required_basis_values(component_id: str, inventory: dict[str, Any]) -> list[int]:
    if component_id == "tgt-ops":
        return list(inventory["service_requirements"]["operations"].values())
    if component_id == "tgt-automation":
        return list(inventory["service_requirements"]["automation"].values())
    if component_id == "tgt-logs":
        return list(inventory["service_requirements"]["logs"].values())
    return []


def verify_sources(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    inventory_sources = index_unique(inventory["source_products"], "inventory_id", "inventory source")
    rules = index_unique(snapshot["product_rules"], "source_inventory_id", "product rule")
    plan_sources = index_unique(plan["sources"], "inventory_id", "plan source")
    require(set(plan_sources) == set(inventory_sources) == set(rules), "plan must name every and only inventoried source product")

    for source_id, source in inventory_sources.items():
        candidate = plan_sources[source_id]
        rule = rules[source_id]
        exact_fields = {
            "product_name": source["product_name"],
            "version": source["version"],
            "target_component_id": rule["target_component_id"],
            "target_component": rule["target_component"],
            "target_version": rule["target_version"],
            "migration_method": rule["migration_method"],
            "support_boundary": rule["support_boundary"],
        }
        for field, expected in exact_fields.items():
            require(candidate[field] == expected, f"{source_id}: incorrect {field}")
        require(candidate["migration_method"] not in rule["forbidden_methods"], f"{source_id}: forbidden migration method")

        inventory_items = index_unique(source["content"], "item_id", f"{source_id} inventory content")
        carried = index_unique(candidate["carry_forward"], "item_id", f"{source_id} carried item")
        abandoned = index_unique(candidate["abandon"], "item_id", f"{source_id} abandoned item")
        require(not (set(carried) & set(abandoned)), f"{source_id}: content appears in both dispositions")
        require(set(carried) | set(abandoned) == set(inventory_items), f"{source_id}: every content item must be dispositioned exactly once")

        rules_by_kind = content_rule_map(rule)
        for item_id, item in inventory_items.items():
            require(item["kind"] in rules_by_kind, f"{source_id}: no pinned compatibility rule for {item['kind']}")
            item_rule = rules_by_kind[item["kind"]]
            if item_rule["disposition"] == "carry":
                require(item_id in carried, f"{source_id}: {item_id} must carry forward")
                require(carried[item_id]["kind"] == item["kind"], f"{source_id}: changed kind for {item_id}")
                require(carried[item_id]["mode"] == item_rule["mode"], f"{source_id}: wrong carry mode for {item_id}")
            else:
                require(item_id in abandoned, f"{source_id}: {item_id} must be abandoned")
                require(abandoned[item_id]["kind"] == item["kind"], f"{source_id}: changed kind for {item_id}")
                require(abandoned[item_id]["reason_code"] == item_rule["reason_code"], f"{source_id}: wrong abandonment boundary for {item_id}")
    return inventory_sources, rules


def verify_targets(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    constraints = index_unique(snapshot["target_component_constraints"], "component_id", "target constraint")
    required_ids = {key for key, value in constraints.items() if value["required"]}
    targets = index_unique(plan["target_components"], "component_id", "target component")
    require(set(targets) == required_ids, "target component set does not match the required VCF architecture")

    fqdn_keys = {
        "tgt-fleet": "fleet",
        "tgt-ops": "operations",
        "tgt-automation": "automation",
        "tgt-logs": "logs",
        "tgt-identity": "identity",
    }
    placement_source = inventory["target_placement"]
    for component_id, target in targets.items():
        constraint = constraints[component_id]
        for field in ("name", "version", "role"):
            require(target[field] == constraint[field], f"{component_id}: incorrect {field}")
        require(target["fqdn"] == placement_source["reserved_fqdns"][fqdn_keys[component_id]], f"{component_id}: FQDN is not the reserved target")

        placement = target["placement"]
        expected_common = {
            "vcf_instance": inventory["vcf"]["primary_instance"],
            "domain": placement_source["domain"],
            "cluster": placement_source["cluster"],
            "datastore": placement_source["datastore"],
            "network": placement_source["network"],
        }
        for field, expected in expected_common.items():
            require(placement[field] == expected, f"{component_id}: incorrect placement {field}")
        fault_domains = placement["fault_domains"]
        require(set(fault_domains) <= set(placement_source["fault_domains"]), f"{component_id}: unknown fault domain")

        profile = select_minimum_profile(constraint, inventory)
        expected_sizing = expected_profile_shape(profile)
        for field, expected in expected_sizing.items():
            require(target["sizing"][field] == expected, f"{component_id}: sizing {field} is not the smallest fitting pinned profile")
        if profile["availability"] == "production-ha":
            require(placement["anti_affinity"] is True, f"{component_id}: HA nodes require anti-affinity")
            require(len(fault_domains) >= 2, f"{component_id}: HA placement must span fault domains")
        else:
            require(len(fault_domains) == 1, f"{component_id}: single-node placement names one fault domain")

        joined_basis = " ".join(target["sizing"]["basis"])
        for value in required_basis_values(component_id, inventory):
            require(str(value) in joined_basis, f"{component_id}: sizing basis omits inventory requirement {value}")
    return targets


def verify_steps(
    plan: dict[str, Any],
    inventory_sources: dict[str, dict[str, Any]],
    product_rules: dict[str, dict[str, Any]],
    targets: dict[str, dict[str, Any]],
    snapshot: dict[str, Any],
) -> None:
    steps = plan["steps"]
    orders = [step["order"] for step in steps]
    require(orders == sorted(orders) and len(orders) == len(set(orders)), "migration step order must be strictly increasing and unique")
    by_id = index_unique(steps, "step_id", "migration step")
    seen_gate_ids: set[str] = set()

    all_content_ids = {
        item["item_id"]
        for source in inventory_sources.values()
        for item in source["content"]
    }
    content_use: Counter[str] = Counter()
    component_orders: dict[str, list[int]] = {component_id: [] for component_id in targets}

    for step in steps:
        require(step["target_component_id"] in targets, f"{step['step_id']}: unknown target component")
        component_orders[step["target_component_id"]].append(step["order"])
        for source_id in step["source_inventory_ids"]:
            require(source_id in inventory_sources, f"{step['step_id']}: unknown source {source_id}")
            require(
                step["target_component_id"] == product_rules[source_id]["target_component_id"],
                f"{step['step_id']}: source is attached to the wrong target component",
            )
        for dependency in step["depends_on"]:
            require(dependency in by_id, f"{step['step_id']}: unknown dependency {dependency}")
            require(by_id[dependency]["order"] < step["order"], f"{step['step_id']}: dependency is not earlier")
        for gate in step["gates"]:
            require(gate["gate_id"] not in seen_gate_ids, f"duplicate gate_id: {gate['gate_id']}")
            seen_gate_ids.add(gate["gate_id"])
        for item_id in step["carry_item_ids"] + step["abandon_item_ids"]:
            require(item_id in all_content_ids, f"{step['step_id']}: unknown content item {item_id}")
            require(
                any(item_id in {item["item_id"] for item in inventory_sources[source_id]["content"]} for source_id in step["source_inventory_ids"]),
                f"{step['step_id']}: content item is not owned by the named source",
            )
            content_use[item_id] += 1

    require(all(component_orders.values()), "every required target component must have a migration step")
    for constraint in snapshot["sequence_constraints"]:
        before = component_orders[constraint["before_component"]]
        after = component_orders[constraint["after_component"]]
        require(max(before) < min(after), f"all {constraint['before_component']} work must precede {constraint['after_component']}")

    for source_id, source in inventory_sources.items():
        source_steps = [step for step in steps if source_id in step["source_inventory_ids"]]
        require(source_steps, f"{source_id}: no migration steps")
        gate_kinds = {gate["kind"] for step in source_steps for gate in step["gates"]}
        missing_kinds = set(product_rules[source_id]["required_gate_kinds"]) - gate_kinds
        require(not missing_kinds, f"{source_id}: missing technical gate kinds {sorted(missing_kinds)}")
        for item in source["content"]:
            require(content_use[item["item_id"]] == 1, f"{source_id}: {item['item_id']} must appear in exactly one ordered step")

    fleet_steps = [step for step in steps if step["target_component_id"] == "tgt-fleet"]
    fleet_gates = [gate for step in fleet_steps for gate in step["gates"]]
    require({"entitlement", "lifecycle", "capacity"} <= {gate["kind"] for gate in fleet_gates}, "fleet bootstrap lacks entitlement, lifecycle, or capacity gates")
    patch_text = " ".join(gate["condition"] + " " + gate["success_evidence"] for gate in fleet_gates).lower()
    require("patch 2" in patch_text, "fleet lifecycle gate must enforce Aria Suite Lifecycle 8.18 Patch 2")
    require("vcf" in patch_text, "fleet entitlement gate must select VCF")

    identity_steps = [step for step in steps if step["target_component_id"] == "tgt-identity"]
    identity_gates = [gate for step in identity_steps for gate in step["gates"]]
    require("identity" in {gate["kind"] for gate in identity_gates}, "identity cutover lacks an identity gate")
    identity_text = " ".join(gate["condition"] + " " + gate["success_evidence"] for gate in identity_gates).lower()
    require("core" in identity_text and "9" in identity_text, "identity gate must wait for VCF core 9")


def verify_research(snapshot: dict[str, Any]) -> None:
    research_path = ROOT / "research.md"
    try:
        research = research_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationError("missing research artifact: research.md") from exc
    require(research.strip(), "research.md is empty")

    access_dates = re.findall(
        r"(?i)\baccess(?:ed|\s+date)?\b[^\n0-9]{0,20}(\d{4}-\d{2}-\d{2})",
        research,
    )
    require(access_dates, "research.md must record an ISO access date")
    try:
        parsed_dates = [date.fromisoformat(value) for value in access_dates]
        snapshot_date = date.fromisoformat(snapshot["as_of"])
    except ValueError as exc:
        raise VerificationError("research.md or snapshot contains an invalid ISO date") from exc
    require(
        any(value >= snapshot_date for value in parsed_dates),
        "research access date predates the pinned compatibility snapshot",
    )

    raw_urls = re.findall(r"(?i)\b(?:https?|file)://[^\s|)>]+", research)
    urls = [value.rstrip(".,;:") for value in raw_urls]
    require(urls, "research.md does not record any consulted URL")
    require(len(urls) == len(set(urls)), "research.md contains duplicate consulted URLs")
    for url in urls:
        parsed = urlparse(url)
        require(parsed.scheme == "https", f"research URL must use HTTPS: {url}")
        host = (parsed.hostname or "").lower()
        official_host = (
            host == "broadcom.com"
            or host.endswith(".broadcom.com")
            or host == "vmware.com"
            or host.endswith(".vmware.com")
            or host == "powershellgallery.com"
            or host.endswith(".powershellgallery.com")
        )
        require(official_host, f"research URL is not a Broadcom-published source: {url}")
        require(parsed.path not in {"", "/"}, f"research URL does not identify a page: {url}")

        # Each source entry must contain human-readable material around the URL,
        # accommodating either a table row or a short labeled Markdown block.
        offset = research.find(url)
        context = research[max(0, offset - 300) : min(len(research), offset + len(url) + 500)]
        context_without_urls = re.sub(r"(?i)\b(?:https?|file)://[^\s|)>]+", "", context)
        words = re.findall(r"[A-Za-z][A-Za-z0-9'-]+", context_without_urls)
        require(len(words) >= 20, f"research entry lacks a page title or conclusion: {url}")

    normalized = research.lower()
    coverage = {
        "supported migration paths": ("upgrade", "migration", "greenfield", "fresh deployment"),
        "content/configuration compatibility": ("content", "configuration", "export", "import", "transfer"),
        "sizing": ("sizing", "capacity", "objects", "metrics"),
        "entitlement behavior": ("entitlement", "license", "licensing"),
        "end-of-support boundaries": ("end of general support", "end-of-support", "support through", "eogs"),
    }
    for topic, terms in coverage.items():
        require(any(term in normalized for term in terms), f"research.md does not cover {topic}")


def powershell_json(script: str, path: Path) -> Any:
    try:
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            cwd=ROOT,
            env={**os.environ, "VCF_VERIFY_PATH": str(path)},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise VerificationError("pwsh is required to validate the PowerShell module") from exc
    require(result.returncode == 0, f"PowerShell validation failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"PowerShell validation returned invalid JSON: {result.stdout!r}") from exc


def generate_plan_with_module(root_module_path: Path) -> dict[str, Any]:
    # The VMware SDK calls required by this task are pure model constructors.
    # Constructor shims let the protected verifier execute the generator without
    # installing or vendoring PowerCLI; they provide no service state or answers.
    script = r"""
function global:Get-Module {
    [CmdletBinding()]
    param(
        [switch]$ListAvailable,
        [Parameter(Position=0)][string[]]$Name,
        [Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments
    )
    if ($ListAvailable -and $Name -and @($Name | Where-Object { $_ -like 'VMware.Sdk.Vcf.*' }).Count -eq $Name.Count) {
        return @($Name | ForEach-Object { [pscustomobject]@{ Name = $_ } })
    }
    Microsoft.PowerShell.Core\Get-Module @PSBoundParameters
}
function global:Import-Module {
    [CmdletBinding()]
    param(
        [Parameter(Position=0)][string[]]$Name,
        [Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments
    )
    if ($Name -and @($Name | Where-Object { $_ -like 'VMware.Sdk.Vcf.*' }).Count -eq $Name.Count) {
        return
    }
    Microsoft.PowerShell.Core\Import-Module @PSBoundParameters
}
function global:Initialize-VcfInstallerVcfOperationsNode {
    param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments)
    [pscustomobject]@{ Constructor = $MyInvocation.MyCommand.Name }
}
function global:Initialize-VcfInstallerVcfOperationsSpec {
    param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments)
    [pscustomobject]@{ Constructor = $MyInvocation.MyCommand.Name }
}
function global:Initialize-VcfInstallerVcfAutomationSpec {
    param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments)
    [pscustomobject]@{ Constructor = $MyInvocation.MyCommand.Name }
}
function global:Initialize-VcfInstallerVcfManagementComponentsNetworkSpec {
    param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments)
    [pscustomobject]@{ Constructor = $MyInvocation.MyCommand.Name }
}
function global:Initialize-VcfInstallerVcfManagementComponentsInfrastructureSpec {
    param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments)
    [pscustomobject]@{ Constructor = $MyInvocation.MyCommand.Name }
}

Microsoft.PowerShell.Core\Import-Module -Name $env:VCF_VERIFY_MODULE -Force
New-VcfAriaMigrationPlan `
    -InventoryPath $env:VCF_VERIFY_INVENTORY `
    -CompatibilityPath $env:VCF_VERIFY_COMPATIBILITY `
    -InstallerSpecPath $env:VCF_VERIFY_INSTALLER `
    -OutputPath $env:VCF_VERIFY_OUTPUT | Out-Null
"""
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        output_path = Path(temporary) / "generated-plan.json"
        environment = {
            **os.environ,
            "VCF_VERIFY_MODULE": str(root_module_path),
            "VCF_VERIFY_INVENTORY": str(ROOT / "estate-inventory.json"),
            "VCF_VERIFY_COMPATIBILITY": str(ROOT / "compatibility-snapshot.json"),
            "VCF_VERIFY_INSTALLER": str(ROOT / "installer-spec.json"),
            "VCF_VERIFY_OUTPUT": str(output_path),
        }
        try:
            result = subprocess.run(
                ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            raise VerificationError("pwsh is required to execute the PowerShell module") from exc
        require(result.returncode == 0, f"PowerShell generator execution failed: {result.stderr.strip()}")
        require(output_path.is_file(), "New-VcfAriaMigrationPlan did not write -OutputPath")
        try:
            return json.loads(output_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise VerificationError("New-VcfAriaMigrationPlan wrote invalid JSON") from exc


def verify_module(plan: dict[str, Any], installer_spec: dict[str, Any]) -> None:
    module_spec = installer_spec["module"]
    manifest_path = ROOT / module_spec["manifest"]
    root_module_path = ROOT / module_spec["root_module"]
    require(manifest_path.is_file(), f"missing module manifest: {module_spec['manifest']}")
    require(root_module_path.is_file(), f"missing module implementation: {module_spec['root_module']}")

    schema_modules = plan["sdk_driver"]["modules"]
    schema_constructors = plan["sdk_driver"]["installer_constructors"]
    require(set(schema_modules) == set(module_spec["required_sdk_modules"]), "plan sdk_driver modules do not match the installer specification")
    require(set(schema_constructors) == set(module_spec["required_installer_constructors"]), "plan sdk_driver constructors do not match the installer specification")

    manifest_script = r"""
$data = Import-PowerShellDataFile -Path $env:VCF_VERIFY_PATH
$required = @($data.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } elseif ($_.ModuleName) { $_.ModuleName } else { [string]$_ }
})
[pscustomobject]@{
    RootModule = $data.RootModule
    RequiredModules = $required
    FunctionsToExport = @($data.FunctionsToExport)
} | ConvertTo-Json -Compress
"""
    manifest = powershell_json(manifest_script, manifest_path)
    require(manifest["RootModule"] == Path(module_spec["root_module"]).name, "manifest RootModule is incorrect")
    require(set(manifest["RequiredModules"]) == set(module_spec["required_sdk_modules"]), "manifest must declare exactly the supplied VMware.Sdk.Vcf prerequisites")
    require(module_spec["exported_function"] in manifest["FunctionsToExport"], "manifest does not export New-VcfAriaMigrationPlan")

    parse_script = r"""
$tokens = $null
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($env:VCF_VERIFY_PATH, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) {
    [pscustomobject]@{ Ok = $false; Errors = @($errors | ForEach-Object { $_.Message }) } | ConvertTo-Json -Compress
} else {
    [pscustomobject]@{ Ok = $true; Errors = @() } | ConvertTo-Json -Compress
}
"""
    parse_result = powershell_json(parse_script, root_module_path)
    require(parse_result["Ok"] is True, f"PowerShell syntax errors: {parse_result['Errors']}")

    implementation = root_module_path.read_text(encoding="utf-8")
    require(re.search(r"function\s+New-VcfAriaMigrationPlan\b", implementation, re.IGNORECASE) is not None, "module function is missing")
    for parameter in module_spec["required_parameters"]:
        require(re.search(rf"\${re.escape(parameter)}\b", implementation, re.IGNORECASE) is not None, f"module omits -{parameter}")
    for module_name in module_spec["required_sdk_modules"]:
        require(module_name in implementation, f"module does not load {module_name}")
    require(re.search(r"\bGet-Module\b", implementation, re.IGNORECASE) is not None, "module does not check SDK prerequisites")
    require(re.search(r"\bImport-Module\b", implementation, re.IGNORECASE) is not None, "module does not import SDK prerequisites")
    for constructor in module_spec["required_installer_constructors"]:
        require(re.search(rf"\b{re.escape(constructor)}\b", implementation) is not None, f"module does not use {constructor}")
    require("ConvertFrom-Json" in implementation and "ConvertTo-Json" in implementation, "module must consume fixtures and write JSON")
    require(re.search(r"\b(?:Connect|Invoke)-Vcf", implementation, re.IGNORECASE) is None, "architecture generator must not connect to or mutate VCF")

    module_dir = ROOT / module_spec["directory"]
    vendored = [path.name for path in module_dir.rglob("*") if path.name.lower().startswith("vmware.sdk.vcf")]
    require(not vendored, f"do not vendor VMware.Sdk.Vcf modules: {vendored}")

    generated_plan = generate_plan_with_module(root_module_path)
    require(generated_plan == plan, "New-VcfAriaMigrationPlan output does not match migration-plan.json")


def verify_semantics(
    plan: dict[str, Any],
    installer_spec: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    require(plan["estate_id"] == inventory["estate_id"], "estate_id does not match inventory")
    require(plan["compatibility_snapshot_id"] == snapshot["snapshot_id"], "plan does not pin the supplied compatibility snapshot")
    require(plan["entitlement_decision"] == expected_entitlement(snapshot), "entitlement decision does not enforce the VCF-only topology")
    require(inventory["vcf"]["entitlement"]["standalone_aria_entitlement_after_cutover"] is False, "protected scenario entitlement changed")

    verify_research(snapshot)

    inventory_sources, product_rules = verify_sources(plan, inventory, snapshot)
    targets = verify_targets(plan, inventory, snapshot)
    verify_steps(plan, inventory_sources, product_rules, targets, snapshot)
    verify_module(plan, installer_spec)


def main() -> int:
    try:
        # Contract phase: the candidate artifact is validated against the schema
        # embedded in the protected installer specification before semantic checks.
        installer_spec = load_json(ROOT / "installer-spec.json")
        plan_path = ROOT / installer_spec["output"]["path"]
        plan = load_json(plan_path)
        plan_schema = installer_spec["output"]["schema"]
        validate_schema(plan, plan_schema, plan_schema)

        # Only after schema success may inventory/snapshot semantics be evaluated.
        inventory = load_json(ROOT / "estate-inventory.json")
        snapshot = load_json(ROOT / "compatibility-snapshot.json")
        verify_semantics(plan, installer_spec, inventory, snapshot)
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF migration architecture is schema-valid and matches the pinned estate compatibility contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
