#!/usr/bin/env python3
"""Offline artifact verifier using only pinned local grading inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PROTECTED_SHA256 = {
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "specifications/vcf-installer/SOURCE.json": "a807b3d52cc71a5ae806444b357fbfee369d0372a700ed8285a7e4819492020a",
    "specifications/vcf-installer/LICENSE.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    "estate-inventory.json": "d347516b85772a5914c3ab76a760c3a0b69a3db54f17889c6a23393825c52760",
    "compatibility-snapshot.json": "cf0ccda66569d39fa7e8f6ef76ee891b2fa46b2861ac0cf1a359b58c168cee7c",
    "migration-plan.schema.json": "8dbb41830673dba114f4edab7a8304014c78c54e149a524cf842d354ff3e431a",
}


class ValidationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {path}: {exc}") from exc


def json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise ValidationError(f"only local schema references are supported: {pointer}")
    value = document
    for token in pointer[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[token]
    return value


def type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def schema_errors(instance: Any, schema: dict[str, Any], document: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the JSON-Schema subset used by the pinned OpenAPI document."""
    if "$ref" in schema:
        return schema_errors(instance, json_pointer(document, schema["$ref"]), document, path)

    errors: list[str] = []
    if instance is None and schema.get("nullable"):
        return errors

    for index, subschema in enumerate(schema.get("allOf", [])):
        errors.extend(schema_errors(instance, subschema, document, f"{path}.allOf[{index}]"))
    if "anyOf" in schema:
        choices = [schema_errors(instance, item, document, path) for item in schema["anyOf"]]
        if all(choice for choice in choices):
            errors.append(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        valid = sum(not schema_errors(instance, item, document, path) for item in schema["oneOf"])
        if valid != 1:
            errors.append(f"{path}: satisfies {valid} oneOf branches, expected exactly one")

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not in enum")

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(type_matches(instance, item) for item in expected):
            errors.append(f"{path}: expected one of types {expected}, got {type(instance).__name__}")
            return errors
    elif expected and not type_matches(instance, expected):
        errors.append(f"{path}: expected {expected}, got {type(instance).__name__}")
        return errors

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                errors.extend(schema_errors(value, properties[key], document, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(schema_errors(value, schema["additionalProperties"], document, f"{path}.{key}"))

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: has more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                errors.extend(schema_errors(value, item_schema, document, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], instance) is None:
                    errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
            except re.error as exc:
                errors.append(f"{path}: invalid pattern in pinned schema: {exc}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: is below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: is above maximum {schema['maximum']}")

    return errors


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValidationError(f"missing required file: {path}") from exc
    except UnicodeDecodeError as exc:
        raise ValidationError(f"required text file is not UTF-8: {path}") from exc


def manifest_value(manifest: str, field: str) -> str:
    match = re.search(
        rf"(?ims)^\s*{re.escape(field)}\s*=\s*(.*?)(?=^\s*[A-Za-z][A-Za-z0-9_]*\s*=|\Z)",
        manifest,
    )
    return match.group(1) if match else ""


def powershell_syntax_errors(paths: list[Path]) -> list[str]:
    executable = shutil.which("pwsh")
    if executable is None:
        return ["PowerShell (pwsh) is required to validate the requested PowerShell module"]
    parser = r"""
$hadErrors = $false
$paths = ConvertFrom-Json -InputObject $env:VCF_ARCHITECTURE_PARSE_PATHS
foreach ($path in $paths) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $path,
        [ref] $tokens,
        [ref] $parseErrors
    ) | Out-Null
    foreach ($parseError in $parseErrors) {
        [Console]::Error.WriteLine("{0}:{1}: {2}", $path, $parseError.Extent.StartLineNumber, $parseError.Message)
        $hadErrors = $true
    }
}
if ($hadErrors) { exit 1 }
"""
    child_environment = os.environ.copy()
    child_environment["VCF_ARCHITECTURE_PARSE_PATHS"] = json.dumps([str(path) for path in paths])
    try:
        result = subprocess.run(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", parser],
            capture_output=True,
            env=child_environment,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"could not parse PowerShell package: {exc}"]
    if result.returncode == 0:
        return []
    detail = result.stderr.strip() or result.stdout.strip() or "unknown parser failure"
    return [f"PowerShell syntax validation failed: {detail}"]


def module_package_errors(root: Path) -> list[str]:
    package = root / "VcfFleetArchitecture"
    manifest_path = package / "VcfFleetArchitecture.psd1"
    module_path = package / "VcfFleetArchitecture.psm1"
    try:
        manifest = load_text(manifest_path)
        module = load_text(module_path)
    except ValidationError as exc:
        return [str(exc)]

    errors = powershell_syntax_errors([manifest_path, module_path])
    root_module = manifest_value(manifest, "RootModule")
    required_modules = manifest_value(manifest, "RequiredModules")
    exported_functions = manifest_value(manifest, "FunctionsToExport")
    require(
        re.search(r"(?i)['\"]VcfFleetArchitecture\.psm1['\"]", root_module) is not None,
        "module manifest RootModule must be VcfFleetArchitecture.psm1",
        errors,
    )
    require(
        "VMware.Sdk.Vcf.Installer".lower() in required_modules.lower(),
        "module manifest must declare VMware.Sdk.Vcf.Installer in RequiredModules",
        errors,
    )
    require(
        "New-VcfFleetArchitecture".lower() in exported_functions.lower()
        or re.search(r"['\"]\*['\"]", exported_functions) is not None,
        "module manifest must export New-VcfFleetArchitecture",
        errors,
    )

    require(
        re.search(r"(?im)^\s*function\s+New-VcfFleetArchitecture\b", module) is not None,
        "module must define New-VcfFleetArchitecture",
        errors,
    )
    for parameter in ("InventoryPath", "CompatibilitySnapshotPath", "OutputPath"):
        require(
            len(re.findall(rf"(?i)\${parameter}\b", module)) >= 2,
            f"New-VcfFleetArchitecture must accept and use ${parameter}",
            errors,
        )
    require(
        "estate-inventory.json" not in module.lower() and "compatibility-snapshot.json" not in module.lower(),
        "module must not hard-code the supplied inventory or compatibility-snapshot paths",
        errors,
    )
    initializer_calls = set(
        re.findall(
            r"(?im)^\s*(?:\$[A-Za-z_][A-Za-z0-9_]*\s*=\s*)?(Initialize-VcfInstaller[A-Za-z0-9_-]+)\b",
            module,
        )
    )
    require(
        len(initializer_calls) >= 2,
        "module must use VMware.Sdk.Vcf.Installer initializer cmdlets to construct the installer model",
        errors,
    )

    if package.is_dir():
        vendored = [
            path.relative_to(package).as_posix()
            for path in package.rglob("*")
            if path.is_file() and path.suffix.lower() in {".dll", ".nupkg"}
        ]
        require(not vendored, f"package must not vendor VMware SDK or PowerCLI binaries: {vendored}", errors)
    return errors


def research_record_errors(root: Path) -> list[str]:
    path = root / "VcfFleetArchitecture" / "research.md"
    try:
        record = load_text(path)
    except ValidationError as exc:
        return [str(exc)]

    errors: list[str] = []
    dates = re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", record)
    valid_dates = []
    for value in dates:
        try:
            valid_dates.append(date.fromisoformat(value))
        except ValueError:
            pass
    require(bool(valid_dates), "research.md must record a valid access date in YYYY-MM-DD form", errors)

    url_pattern = re.compile(r"https://[^\s<>)\]}]+")
    urls: list[str] = []
    for line_number, line in enumerate(record.splitlines(), start=1):
        matches = url_pattern.findall(line)
        for raw_url in matches:
            url = raw_url.rstrip(".,;:")
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            require(
                host == "broadcom.com" or host.endswith(".broadcom.com"),
                f"research.md line {line_number} must use a real Broadcom-published HTTPS source",
                errors,
            )
            context = line.replace(raw_url, " ").strip(" -#*\u2014:.,;\t")
            require(
                "broadcom" in context.lower() and len(context) >= 40,
                f"research.md line {line_number} must identify the source title, Broadcom publisher, and informed decision",
                errors,
            )
            urls.append(url)
    require(len(set(urls)) >= 2, "research.md must record at least two distinct live Broadcom sources", errors)
    return errors


def verify(root: Path) -> list[str]:
    artifact_path = root / "VcfFleetArchitecture" / "architecture.json"
    spec_path = root / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"

    # The first artifact check is intentionally validation against the installer
    # specification's own SddcSpec schema.  Do not move domain checks above it.
    artifact = load_json(artifact_path)
    installer = load_json(spec_path)
    try:
        sddc_schema = installer["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError) as exc:
        raise ValidationError("pinned installer document does not contain components.schemas.SddcSpec") from exc
    first_errors = schema_errors(artifact, sddc_schema, installer)
    if first_errors:
        return ["installer SddcSpec validation failed: " + item for item in first_errors]

    errors: list[str] = []
    for relative, expected in PROTECTED_SHA256.items():
        path = root / relative
        require(path.is_file(), f"protected file is missing: {relative}", errors)
        if path.is_file():
            require(sha256(path) == expected, f"protected file was modified: {relative}", errors)
    if errors:
        return errors

    inventory = load_json(root / "estate-inventory.json")
    snapshot = load_json(root / "compatibility-snapshot.json")
    plan_schema = load_json(root / "migration-plan.schema.json")
    extension = artifact.get("x-vcfFleetArchitecture")
    if not isinstance(extension, dict):
        return ["artifact must contain object property x-vcfFleetArchitecture"]
    errors.extend("architecture extension schema: " + item for item in schema_errors(extension, plan_schema, plan_schema))
    if errors:
        return errors

    errors.extend(module_package_errors(root))
    errors.extend(research_record_errors(root))

    resources = inventory["availableGreenfieldResources"]
    management = snapshot["managementDomain"]
    require(artifact.get("sddcId") == resources["sddcId"], "SddcSpec.sddcId must use the available greenfield resource", errors)
    require(artifact.get("workflowType") == management["workflowType"], "SddcSpec.workflowType does not match the pinned management-domain design", errors)
    require(artifact.get("version") == snapshot["targetVcfVersion"], "SddcSpec.version must be the pinned VCF target", errors)
    require(artifact.get("vcfInstanceName") == resources["vcfInstanceName"], "SddcSpec.vcfInstanceName must match the inventory", errors)

    host_names = [item.get("hostname") for item in artifact.get("hostSpecs", [])]
    require(host_names == resources["managementHosts"], "SddcSpec.hostSpecs must name all available greenfield management hosts in inventory order", errors)
    require(len(host_names) >= management["minimumHostCount"], "SddcSpec has too few management hosts", errors)

    network_projection = [
        {key: item.get(key) for key in ("networkType", "vlanId", "subnet", "gateway", "mtu")}
        for item in artifact.get("networkSpecs", [])
    ]
    require(network_projection == resources["networks"], "SddcSpec.networkSpecs must use all pinned available networks", errors)
    require(artifact.get("dnsSpec", {}).get("subdomain") == resources["dnsSubdomain"], "SddcSpec DNS subdomain mismatch", errors)
    require(artifact.get("dnsSpec", {}).get("nameservers") == resources["dnsServers"], "SddcSpec DNS servers mismatch", errors)
    require(artifact.get("ntpServers") == resources["ntpServers"], "SddcSpec NTP servers mismatch", errors)

    vcenter = artifact.get("vcenterSpec", {})
    require(vcenter.get("version") == management["vcenterVersion"], "greenfield vCenter version does not match the pinned BOM", errors)
    require(vcenter.get("useExistingDeployment") is False, "management vCenter must be a greenfield deployment", errors)
    require(isinstance(vcenter.get("rootVcenterPassword"), str) and vcenter["rootVcenterPassword"].startswith("{{") and vcenter["rootVcenterPassword"].endswith("}}"), "vCenter credential must be a placeholder", errors)
    nsx = artifact.get("nsxtSpec", {})
    require(nsx.get("version") == management["nsxVersion"], "greenfield NSX version does not match the pinned BOM", errors)
    require(nsx.get("useExistingDeployment") is False, "management NSX must be a greenfield deployment", errors)
    require(artifact.get("sddcManagerSpec", {}).get("version") == management["sddcManagerVersion"], "SDDC Manager version does not match the pinned BOM", errors)
    vsan = artifact.get("datastoreSpec", {}).get("vsanSpec", {})
    require(vsan.get("esaConfig", {}).get("enabled") is True, "management datastore must use vSAN ESA", errors)
    require(vsan.get("failuresToTolerate") == management["failuresToTolerate"], "vSAN failuresToTolerate mismatch", errors)

    require(extension["estateId"] == inventory["estateId"], "architecture estateId mismatch", errors)
    require(extension["targetFleet"] == inventory["targetFleet"], "architecture targetFleet mismatch", errors)
    require(extension["targetVcfVersion"] == inventory["targetVcfVersion"] == snapshot["targetVcfVersion"], "architecture VCF target mismatch", errors)
    require(extension["workloadDomain"]["sourceVcenter"] == "vcsa-chi-01", "workload-domain source vCenter mismatch", errors)

    inventory_by_id = {item["componentId"]: item for item in inventory["components"]}
    plan = extension["migrationPlan"]
    plan_ids = [item["componentId"] for item in plan]
    require(plan_ids == snapshot["migrationOrder"], "migration steps are not in the pinned compatibility order", errors)
    require(len(plan_ids) == len(set(plan_ids)), "migration plan contains duplicate component IDs", errors)
    require(set(plan_ids) == set(inventory_by_id), "migration plan must name every and only inventory component", errors)
    for index, step in enumerate(plan, start=1):
        component_id = step["componentId"]
        if component_id not in inventory_by_id or component_id not in snapshot["componentRules"]:
            continue
        fixture = inventory_by_id[component_id]
        rule = snapshot["componentRules"][component_id]
        require(step["order"] == index, f"{component_id}: order must be {index}", errors)
        require(step["componentType"] == fixture["componentType"] == rule["componentType"], f"{component_id}: component type mismatch", errors)
        require(step["sourceVersion"] == fixture["version"] == rule["sourceVersion"], f"{component_id}: source version mismatch", errors)
        for field in ("targetVersion", "versionPath", "action", "gates"):
            require(step[field] == rule[field], f"{component_id}: {field} does not match pinned compatibility rule", errors)
        require(step["versionPath"][0] == step["sourceVersion"] and step["versionPath"][-1] == step["targetVersion"], f"{component_id}: versionPath endpoints mismatch", errors)

    products = {item["product"]: item for item in extension["managementProducts"]}
    require(set(products) == set(snapshot["managementProducts"]), "architecture must place exactly the three pinned management products", errors)
    for product, expected in snapshot["managementProducts"].items():
        if product not in products:
            continue
        for field in ("targetVersion", "location", "nodeCount", "size", "sizingBasis"):
            require(products[product][field] == expected[field], f"{product}: {field} does not match pinned sizing", errors)
    ops_expected = snapshot["managementProducts"]["vcf-operations"]
    require(len(artifact.get("vcfOperationsSpec", {}).get("nodes", [])) == ops_expected["nodeCount"], "SddcSpec VCF Operations node count mismatch", errors)
    require(artifact.get("vcfOperationsSpec", {}).get("applianceSize") == ops_expected["size"], "SddcSpec VCF Operations size mismatch", errors)
    require(artifact.get("vcfOperationsSpec", {}).get("version") == ops_expected["targetVersion"], "SddcSpec VCF Operations target mismatch", errors)
    auto_expected = snapshot["managementProducts"]["vcf-automation"]
    require(artifact.get("vcfAutomationSpec", {}).get("size") == auto_expected["size"], "SddcSpec VCF Automation size mismatch", errors)
    require(artifact.get("vcfAutomationSpec", {}).get("version") == auto_expected["targetVersion"], "SddcSpec VCF Automation target mismatch", errors)

    return errors


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    try:
        errors = verify(root)
    except ValidationError as exc:
        errors = [str(exc)]
    except (KeyError, TypeError, IndexError) as exc:
        errors = [f"artifact structure is incomplete: {exc}"]
    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: installer-valid SddcSpec with pinned brownfield plan and management-product sizing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
