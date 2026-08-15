#!/usr/bin/env python3
"""Deterministic verification for the VCF 9.1 architecture seed."""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def load_json(relative_path: str):
    path = ROOT / relative_path
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        FAILURES.append(f"missing required file: {relative_path}")
    except json.JSONDecodeError as exc:
        FAILURES.append(f"invalid JSON in {relative_path}: {exc}")
    return None


def resolve_pointer(document, pointer: str):
    value = document
    for raw_part in pointer.lstrip("#/").split("/"):
        part = unquote(raw_part).replace("~1", "/").replace("~0", "~")
        value = value[part]
    return value


def is_json_type(value, type_name: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(type_name, True)


def schema_errors(instance, schema, document, path: str = "$") -> list[str]:
    """Validate the JSON Schema features used by the two protected schemas."""
    if not isinstance(schema, dict):
        return []
    if "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/"):
            return [f"{path}: unsupported external schema reference {reference}"]
        try:
            schema = resolve_pointer(document, reference)
        except (KeyError, TypeError):
            return [f"{path}: unresolved schema reference {reference}"]

    errors: list[str] = []
    for subschema in schema.get("allOf", []):
        errors.extend(schema_errors(instance, subschema, document, path))
    if "anyOf" in schema:
        choices = [schema_errors(instance, item, document, path) for item in schema["anyOf"]]
        if choices and all(choice for choice in choices):
            errors.append(f"{path}: does not satisfy any allowed schema")
    if "oneOf" in schema:
        matches = sum(not schema_errors(instance, item, document, path) for item in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: must satisfy exactly one allowed schema")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(is_json_type(instance, item) for item in allowed_types):
            return errors + [f"{path}: expected type {expected_type}, got {type(instance).__name__}"]

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value is not in the allowed enumeration")

    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                errors.append(f"{path}: missing required property {required}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            child_path = f"{path}.{name}"
            if name in properties:
                errors.extend(schema_errors(value, properties[name], document, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child_path}: additional property is forbidden")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    schema_errors(value, schema["additionalProperties"], document, child_path)
                )
        if len(instance) < schema.get("minProperties", 0):
            errors.append(f"{path}: too few properties")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True) for item in instance]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: array items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, value in enumerate(instance):
                errors.extend(schema_errors(value, schema["items"], document, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string is too long")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance) is not None
            except re.error as exc:
                errors.append(f"{path}: protected schema has invalid pattern: {exc}")
            else:
                if not matched:
                    errors.append(f"{path}: string does not match required pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: number is above maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(exclusive_minimum, (int, float)) and not isinstance(exclusive_minimum, bool):
            if instance <= exclusive_minimum:
                errors.append(f"{path}: number is not above exclusive minimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(exclusive_maximum, (int, float)) and not isinstance(exclusive_maximum, bool):
            if instance >= exclusive_maximum:
                errors.append(f"{path}: number is not below exclusive maximum")
        if "multipleOf" in schema:
            quotient = instance / schema["multipleOf"]
            if not math.isclose(quotient, round(quotient)):
                errors.append(f"{path}: number is not a required multiple")
    return errors


def run_module(requirements_path: Path, inventory_path: Path, compatibility_path: Path):
    module_path = ROOT / "src/VcfArchitecture/VcfArchitecture.psm1"
    if not module_path.is_file():
        FAILURES.append("missing PowerShell implementation module")
        return None, None

    wrapper = r'''
param(
    [Parameter(Mandatory)] [string] $ModulePath,
    [Parameter(Mandatory)] [string] $RequirementsPath,
    [Parameter(Mandatory)] [string] $InventoryPath,
    [Parameter(Mandatory)] [string] $CompatibilityPath,
    [Parameter(Mandatory)] [string] $GreenfieldOutput,
    [Parameter(Mandatory)] [string] $MigrationOutput
)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $ModulePath, [ref] $tokens, [ref] $parseErrors
)
if ($parseErrors.Count -gt 0) {
    throw ('PowerShell parse errors: ' + (($parseErrors.Message) -join '; '))
}
$constructorCommands = @(
    $ast.FindAll(
        { param($node) $node -is [System.Management.Automation.Language.CommandAst] },
        $true
    ) | ForEach-Object { $_.GetCommandName() } |
        Where-Object { $_ -like 'Initialize-VcfInstaller*' } |
        Sort-Object -Unique
)
if ($constructorCommands -notcontains 'Initialize-VcfInstallerSddcSpec') {
    throw 'New-VcfGreenfieldSddcSpec must call Initialize-VcfInstallerSddcSpec.'
}
if ($constructorCommands.Count -lt 6) {
    throw 'The installer portion must be constructed with the generated VCF Installer models.'
}

# Constructor doubles let this protected, offline verification execute the module
# without redistributing PowerCLI. They accept the real call shape and return an
# opaque model object; artifact grading below is independent of these objects.
$global:VcfConstructorCalls = [System.Collections.Generic.List[string]]::new()
foreach ($constructorCommand in $constructorCommands) {
    $constructorDouble = [scriptblock]::Create(@"
param([Parameter(ValueFromRemainingArguments = `$true)] [object[]] `$Arguments)
`$global:VcfConstructorCalls.Add('$constructorCommand')
[pscustomobject]@{}
"@)
    Set-Item -Path "Function:global:$constructorCommand" -Value $constructorDouble
}

Import-Module -Name $ModulePath -Force -ErrorAction Stop
$greenfield = Get-Command New-VcfGreenfieldSddcSpec -Module VcfArchitecture -ErrorAction Stop
$migration = Get-Command New-VcfMigrationPlan -Module VcfArchitecture -ErrorAction Stop
$expectedGreenfieldParameters = @('RequirementsPath', 'OutputPath')
$expectedMigrationParameters = @('InventoryPath', 'CompatibilityPath', 'OutputPath')
foreach ($parameterName in $expectedGreenfieldParameters) {
    if (-not $greenfield.Parameters.ContainsKey($parameterName)) {
        throw "New-VcfGreenfieldSddcSpec is missing parameter $parameterName."
    }
}
foreach ($parameterName in $expectedMigrationParameters) {
    if (-not $migration.Parameters.ContainsKey($parameterName)) {
        throw "New-VcfMigrationPlan is missing parameter $parameterName."
    }
}
New-VcfGreenfieldSddcSpec -RequirementsPath $RequirementsPath -OutputPath $GreenfieldOutput | Out-Null
if ($global:VcfConstructorCalls -notcontains 'Initialize-VcfInstallerSddcSpec') {
    throw 'The greenfield command did not execute Initialize-VcfInstallerSddcSpec.'
}
if (@($global:VcfConstructorCalls | Sort-Object -Unique).Count -lt 6) {
    throw 'The greenfield command did not execute the generated installer-model constructors.'
}
New-VcfMigrationPlan -InventoryPath $InventoryPath -CompatibilityPath $CompatibilityPath -OutputPath $MigrationOutput | Out-Null
'''

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_directory:
        temporary = Path(temp_directory)
        wrapper_path = temporary / "run-module.ps1"
        greenfield_output = temporary / "greenfield.json"
        migration_output = temporary / "migration.json"
        wrapper_path.write_text(wrapper, encoding="utf-8")
        command = [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(wrapper_path),
            "-ModulePath",
            str(module_path),
            "-RequirementsPath",
            str(requirements_path),
            "-InventoryPath",
            str(inventory_path),
            "-CompatibilityPath",
            str(compatibility_path),
            "-GreenfieldOutput",
            str(greenfield_output),
            "-MigrationOutput",
            str(migration_output),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=45,
                check=False,
            )
        except FileNotFoundError:
            FAILURES.append("pwsh is required to verify the PowerShell module")
            return None, None
        except subprocess.TimeoutExpired:
            FAILURES.append("PowerShell module execution timed out")
            return None, None
        if completed.returncode != 0:
            tail = completed.stdout[-4000:].strip()
            FAILURES.append(f"PowerShell module execution failed:\n{tail}")
            return None, None
        try:
            greenfield = json.loads(greenfield_output.read_text(encoding="utf-8-sig"))
            migration = json.loads(migration_output.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            FAILURES.append(f"PowerShell commands did not produce valid JSON: {exc}")
            return None, None
        return greenfield, migration


def expected_network(network: dict) -> dict:
    return {
        "networkType": network["networkType"],
        "subnet": network["subnet"],
        "gateway": network["gateway"],
        "subnetMask": network["subnetMask"],
        "includeIpAddressRanges": [
            {
                "startIpAddress": network["startIpAddress"],
                "endIpAddress": network["endIpAddress"],
            }
        ],
        "vlanId": network["vlanId"],
        "mtu": network["mtu"],
    }


def validate_greenfield(document, requirements, compatibility, openapi, label: str) -> None:
    if not isinstance(document, dict):
        check(False, f"{label}: greenfield artifact must be a JSON object")
        return
    schema = openapi["components"]["schemas"]["SddcSpec"]
    for error in schema_errors(document, schema, openapi):
        FAILURES.append(f"{label}: SddcSpec schema violation: {error}")

    check(document.get("sddcId") == requirements["designId"], f"{label}: wrong sddcId")
    check(
        document.get("workflowType") == requirements["workflowType"],
        f"{label}: wrong workflow type",
    )
    check(document.get("version") == requirements["targetVersion"], f"{label}: wrong version")
    check(
        document.get("vcfInstanceName") == requirements["vcfInstanceName"],
        f"{label}: wrong VCF instance name",
    )

    hostnames = [hostname for site in requirements["dataSites"] for hostname in site["hosts"]]
    expected_hosts = [{"hostname": hostname} for hostname in hostnames]
    check(document.get("hostSpecs") == expected_hosts, f"{label}: hostSpecs must be the eight data hosts")

    appliances = requirements["appliances"]
    credentials = requirements["placeholderCredentials"]
    expected_vcenter = {
        "vcenterHostname": appliances["vcenterHostname"],
        "rootVcenterPassword": credentials["vcenterRootPassword"],
        "vmSize": appliances["vcenterVmSize"],
        "storageSize": appliances["vcenterStorageSize"],
    }
    check(document.get("vcenterSpec") == expected_vcenter, f"{label}: wrong vCenter spec")
    check(
        document.get("clusterSpec")
        == {
            "datacenterName": requirements["managementDomain"]["datacenterName"],
            "clusterName": requirements["managementDomain"]["clusterName"],
        },
        f"{label}: wrong cluster spec",
    )

    switch = requirements["managementDomain"]["distributedSwitch"]
    check(
        document.get("dvsSpecs")
        == [
            {
                "dvsName": switch["name"],
                "networks": [network["networkType"] for network in requirements["networks"]],
                "mtu": switch["mtu"],
                "vmnicsToUplinks": switch["vmnicsToUplinks"],
            }
        ],
        f"{label}: wrong distributed-switch design",
    )
    check(
        document.get("networkSpecs") == [expected_network(item) for item in requirements["networks"]],
        f"{label}: wrong SddcSpec network projection",
    )
    check(
        document.get("dnsSpec") == requirements["dns"],
        f"{label}: wrong DNS spec",
    )
    check(document.get("ntpServers") == requirements["ntpServers"], f"{label}: wrong NTP servers")

    expected_manager = {
        "rootPassword": credentials["sddcManagerRootPassword"],
        "hostname": appliances["sddcManagerHostname"],
        "sshPassword": credentials["sddcManagerSshPassword"],
        "localUserPassword": credentials["sddcManagerLocalUserPassword"],
    }
    check(document.get("sddcManagerSpec") == expected_manager, f"{label}: wrong SDDC Manager spec")
    check(
        document.get("managementPoolName") == requirements["managementDomain"]["managementPoolName"],
        f"{label}: wrong management pool name",
    )
    for option, value in requirements["installerOptions"].items():
        check(document.get(option) == value, f"{label}: wrong installer option {option}")

    nsx = document.get("nsxtSpec", {})
    check(
        nsx.get("nsxtManagers") == [{"hostname": item} for item in appliances["nsxManagers"]],
        f"{label}: wrong NSX manager nodes",
    )
    check(nsx.get("nsxtManagerSize") == appliances["nsxtManagerSize"], f"{label}: wrong NSX size")
    check(nsx.get("vipFqdn") == appliances["nsxVipFqdn"], f"{label}: wrong NSX VIP")
    check(nsx.get("rootNsxtManagerPassword") == credentials["nsxRootPassword"], f"{label}: wrong NSX root credential")
    check(nsx.get("nsxtAdminPassword") == credentials["nsxAdminPassword"], f"{label}: wrong NSX admin credential")
    check(nsx.get("nsxtAuditPassword") == credentials["nsxAuditPassword"], f"{label}: wrong NSX audit credential")
    tep = requirements["nsxHostTepPool"]
    check(nsx.get("transportVlanId") == tep["transportVlanId"], f"{label}: wrong TEP VLAN")
    check(
        nsx.get("ipAddressPoolSpec")
        == {
            "name": tep["name"],
            "subnets": [
                {
                    "ipAddressPoolRanges": [{"start": tep["start"], "end": tep["end"]}],
                    "cidr": tep["cidr"],
                    "gateway": tep["gateway"],
                }
            ],
        },
        f"{label}: wrong NSX TEP pool",
    )

    architecture = document.get("architecture")
    if not isinstance(architecture, dict):
        check(False, f"{label}: missing architecture extension")
        return
    check(
        {"topology", "dataSites", "witness", "availability", "capacity", "connectivity"}
        .issubset(architecture),
        f"{label}: architecture extension is missing required sections",
    )
    check(architecture.get("topology") == "STRETCHED_MANAGEMENT_DOMAIN", f"{label}: wrong topology")
    expected_sites = [
        {
            "siteId": site["siteId"],
            "location": site["location"],
            "faultDomain": site["faultDomain"],
            "preferred": site["preferred"],
            "hosts": site["hosts"],
        }
        for site in requirements["dataSites"]
    ]
    actual_sites = architecture.get("dataSites", [])
    actual_sites_by_id = {
        site.get("siteId"): site for site in actual_sites if isinstance(site, dict)
    }
    check(
        len(actual_sites) == len(expected_sites)
        and set(actual_sites_by_id) == {site["siteId"] for site in expected_sites},
        f"{label}: wrong data fault domains",
    )
    for expected_site in expected_sites:
        actual_site = actual_sites_by_id.get(expected_site["siteId"], {})
        check(
            all(actual_site.get(key) == value for key, value in expected_site.items()),
            f"{label}: wrong data fault-domain values for {expected_site['siteId']}",
        )

    witness_input = requirements["witness"]
    expected_witness = {
        **witness_input,
        "clusterMembership": "WITNESS_ONLY",
        "countsAsDataHost": False,
    }
    actual_witness = architecture.get("witness", {})
    check(
        all(actual_witness.get(key) == value for key, value in expected_witness.items()),
        f"{label}: wrong independent witness",
    )
    witness_name = witness_input["fqdn"]
    check(witness_name not in hostnames, f"{label}: witness appears in hostSpecs")
    check(
        all(witness_name not in site["hosts"] for site in architecture.get("dataSites", [])),
        f"{label}: witness appears in a data fault domain",
    )

    availability = requirements["availability"]
    actual_availability = architecture.get("availability", {})
    check(
        all(actual_availability.get(key) == value for key, value in availability.items()),
        f"{label}: wrong availability policy",
    )
    ftt = availability["hostFailuresToTolerate"]
    datastore_ftt = (
        document.get("datastoreSpec", {}).get("vsanSpec", {}).get("failuresToTolerate")
    )
    check(datastore_ftt == ftt, f"{label}: vSAN and architecture FTT disagree")
    constraints = compatibility["stretchedClusterConstraints"]
    minimum_per_site = 2 * ftt + 1
    check(
        all(minimum_per_site <= len(site["hosts"]) <= constraints["maximumDataHostsPerSite"] for site in requirements["dataSites"]),
        f"{label}: data-site host count cannot support the selected FTT",
    )

    profile = requirements["hostProfile"]
    total_count = len(hostnames)
    remaining_count = min(len(site["hosts"]) for site in requirements["dataSites"]) - ftt
    expected_capacity = {
        "total": {
            "dataHostCount": total_count,
            "cpuCores": total_count * profile["cpuCores"],
            "memoryGiB": total_count * profile["memoryGiB"],
            "rawStorageTiB": total_count * profile["rawStorageTiB"],
        },
        "afterOneSiteAndHostFailures": {
            "dataHostCount": remaining_count,
            "cpuCores": remaining_count * profile["cpuCores"],
            "memoryGiB": remaining_count * profile["memoryGiB"],
            "rawStorageTiB": remaining_count * profile["rawStorageTiB"],
        },
    }
    actual_capacity = architecture.get("capacity", {})
    check(
        all(
            isinstance(actual_capacity.get(section), dict)
            and all(actual_capacity[section].get(key) == value for key, value in values.items())
            for section, values in expected_capacity.items()
        ),
        f"{label}: wrong capacity calculation",
    )
    minimum_capacity = requirements["capacity"]["minimumAfterSiteAndHostFailure"]
    post_failure = expected_capacity["afterOneSiteAndHostFailures"]
    check(
        all(post_failure[key] >= value for key, value in minimum_capacity.items()),
        f"{label}: post-failure capacity is below requirements",
    )
    actual_connectivity = architecture.get("connectivity", {})
    check(
        all(
            isinstance(actual_connectivity.get(link), dict)
            and all(actual_connectivity[link].get(key) == value for key, value in values.items())
            for link, values in requirements["connectivity"].items()
        ),
        f"{label}: wrong inter-site or witness connectivity",
    )
    data_link = requirements["connectivity"]["dataSiteToDataSite"]
    witness_link = requirements["connectivity"]["dataSiteToWitness"]
    check(
        data_link["bandwidthGbps"] >= constraints["minimumDataSiteBandwidthGbps"]
        and data_link["rttMs"] < constraints["maximumDataSiteRttMsExclusive"]
        and witness_link["rttMs"] < constraints["maximumWitnessRttMsExclusive"],
        f"{label}: stretched-cluster links violate pinned constraints",
    )

    allowed_credentials = set(credentials.values())

    def inspect_credentials(value, path="$" ):
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if "password" in key.lower():
                    check(
                        child in allowed_credentials,
                        f"{label}: {child_path} is not a supplied placeholder credential",
                    )
                inspect_credentials(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect_credentials(child, f"{path}[{index}]")

    inspect_credentials(document)


def validate_migration(document, inventory, compatibility, migration_schema, label: str) -> None:
    if not isinstance(document, dict):
        check(False, f"{label}: migration artifact must be a JSON object")
        return
    for error in schema_errors(document, migration_schema, migration_schema):
        FAILURES.append(f"{label}: migration schema violation: {error}")
    check(document.get("schemaVersion") == "1.0", f"{label}: wrong migration schema version")
    check(document.get("estateId") == inventory["estateId"], f"{label}: wrong estate ID")
    check(
        document.get("targetVcfVersion") == inventory["targetVcfVersion"] == compatibility["targetVcfVersion"],
        f"{label}: wrong target VCF version",
    )
    check(
        document.get("compatibilitySnapshotId") == compatibility["snapshotId"],
        f"{label}: wrong compatibility snapshot ID",
    )

    steps = document.get("steps", [])
    components = {item["id"]: item for item in inventory["components"]}
    transitions = {item["componentId"]: item for item in compatibility["transitions"]}
    check(len(components) == len(inventory["components"]), f"{label}: inventory contains duplicate IDs")
    check(len(transitions) == len(compatibility["transitions"]), f"{label}: snapshot contains duplicate transitions")
    step_ids = [step.get("componentId") for step in steps if isinstance(step, dict)]
    check(
        len(steps) == len(components) and set(step_ids) == set(components) and len(step_ids) == len(set(step_ids)),
        f"{label}: migration must contain exactly one step per inventory component",
    )
    check(
        [step.get("order") for step in steps if isinstance(step, dict)] == list(range(1, len(steps) + 1)),
        f"{label}: migration orders must be contiguous and match array order",
    )

    known_gate_ids = {gate["id"] for gate in compatibility["gates"]}
    for step in steps:
        if not isinstance(step, dict) or step.get("componentId") not in components:
            continue
        component = components[step["componentId"]]
        transition = transitions.get(step["componentId"])
        check(transition is not None, f"{label}: missing pinned transition for {step['componentId']}")
        if transition is None:
            continue
        expected = {
            "component": component["name"],
            "currentVersion": component["currentVersion"],
            "targetVersion": transition["targetVersion"],
            "action": transition["action"],
            "viaVersions": transition["viaVersions"],
            "gates": transition["requiredGates"],
        }
        for key, value in expected.items():
            check(step.get(key) == value, f"{label}: {step['componentId']} has wrong {key}")
        check(
            set(step.get("gates", [])).issubset(known_gate_ids),
            f"{label}: {step['componentId']} uses a gate absent from the snapshot",
        )

    positions = {component_id: index for index, component_id in enumerate(step_ids)}
    for rule in compatibility["orderingRules"]:
        check(
            rule["before"] in positions
            and rule["after"] in positions
            and positions[rule["before"]] < positions[rule["after"]],
            f"{label}: precedence rule {rule['before']} -> {rule['after']} is violated",
        )


def validate_research() -> None:
    path = ROOT / "research/sources.md"
    if not path.is_file():
        check(False, "missing research/sources.md")
        return
    text = path.read_text(encoding="utf-8-sig")
    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if date_match:
        try:
            dt.date.fromisoformat(date_match.group(1))
        except ValueError:
            check(False, "research access date is not a real ISO date")
    else:
        check(False, "research sources must include an ISO access date")

    url_pattern = re.compile(r"https://[^\s)>\]]+")
    urls = []
    for match in url_pattern.finditer(text):
        url = match.group(0).rstrip(".,;—|")
        if url not in urls:
            urls.append(url)
        before = re.sub(r"[#*_|`\-]", "", text[max(0, match.start() - 240) : match.start()]).strip()
        after = re.sub(r"[#*_|`—\-]", "", text[match.end() : min(len(text), match.end() + 360)]).strip()
        check(len(before) >= 8, f"research URL lacks a page title: {url}")
        check(len(after) >= 20, f"research URL lacks a specific fact used: {url}")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        official = (
            host == "broadcom.com"
            or host.endswith(".broadcom.com")
            or host == "vmware.com"
            or host.endswith(".vmware.com")
            or (host == "github.com" and parsed.path.startswith("/vmware/"))
        )
        check(official, f"research source is not an official Broadcom/VMware publication: {url}")
        check(parsed.scheme == "https" and ".invalid" not in host, f"research URL is not reachable-form HTTPS: {url}")
    check(len(urls) >= 2, "research must record distinct live sources")
    lowered = text.lower()
    check(
        ("compatib" in lowered or "interoperab" in lowered)
        and "upgrade" in lowered
        and "stretch" in lowered
        and "vsan" in lowered,
        "research record does not cover compatibility/interoperability, upgrade paths, and stretched vSAN",
    )


def main() -> int:
    requirements = load_json("fixtures/greenfield-requirements.json")
    inventory = load_json("fixtures/estate-inventory.json")
    compatibility = load_json("fixtures/compatibility-snapshot.json")
    migration_schema = load_json("schemas/migration-plan.schema.json")
    openapi = load_json("specifications/vcf-installer/vcf-installer-openapi.json")
    if any(item is None for item in (requirements, inventory, compatibility, migration_schema, openapi)):
        for failure in FAILURES:
            print(f"FAIL: {failure}")
        return 1

    generated_greenfield, generated_migration = run_module(
        ROOT / "fixtures/greenfield-requirements.json",
        ROOT / "fixtures/estate-inventory.json",
        ROOT / "fixtures/compatibility-snapshot.json",
    )
    checked_greenfield = load_json("artifacts/greenfield-sddc-spec.json")
    checked_migration = load_json("artifacts/migration-plan.json")

    if generated_greenfield is not None:
        validate_greenfield(generated_greenfield, requirements, compatibility, openapi, "generated")
    if generated_migration is not None:
        validate_migration(generated_migration, inventory, compatibility, migration_schema, "generated")
    if checked_greenfield is not None:
        validate_greenfield(checked_greenfield, requirements, compatibility, openapi, "checked-in")
    if checked_migration is not None:
        validate_migration(checked_migration, inventory, compatibility, migration_schema, "checked-in")
    if generated_greenfield is not None and checked_greenfield is not None:
        check(generated_greenfield == checked_greenfield, "checked-in greenfield artifact is stale")
    if generated_migration is not None and checked_migration is not None:
        check(generated_migration == checked_migration, "checked-in migration artifact is stale")

    # Exercise both path-taking commands with a second valid input set so a
    # checked-in answer or fixture-specific constant cannot stand in for the
    # requested generators.
    if generated_greenfield is not None and generated_migration is not None:
        dynamic_requirements = json.loads(json.dumps(requirements))
        dynamic_inventory = json.loads(json.dumps(inventory))
        dynamic_compatibility = json.loads(json.dumps(compatibility))
        dynamic_requirements["designId"] = "chi02-m02"
        dynamic_requirements["vcfInstanceName"] = "CHI-Metro-VCF-Validation"
        dynamic_requirements["hostProfile"]["cpuCores"] = 72
        dynamic_requirements["dataSites"][0]["location"] = "Chicago-A-Validation"
        dynamic_inventory["estateId"] = "chi-vcf-validation"
        for component in dynamic_inventory["components"]:
            if component["id"] == "vcenter":
                component["name"] = "VMware vCenter Server Validation"
        dynamic_compatibility["snapshotId"] = "broadcom-vcf-9.1.0.0-validation"
        for transition in dynamic_compatibility["transitions"]:
            if transition["componentId"] == "vcenter":
                transition["requiredGates"] = list(reversed(transition["requiredGates"]))
        with tempfile.TemporaryDirectory(prefix="vcfarch-inputs-") as input_directory:
            inputs = Path(input_directory)
            requirements_path = inputs / "requirements.json"
            inventory_path = inputs / "inventory.json"
            compatibility_path = inputs / "compatibility.json"
            requirements_path.write_text(json.dumps(dynamic_requirements), encoding="utf-8")
            inventory_path.write_text(json.dumps(dynamic_inventory), encoding="utf-8")
            compatibility_path.write_text(json.dumps(dynamic_compatibility), encoding="utf-8")
            dynamic_greenfield, dynamic_migration = run_module(
                requirements_path, inventory_path, compatibility_path
            )
        if dynamic_greenfield is not None:
            validate_greenfield(
                dynamic_greenfield,
                dynamic_requirements,
                dynamic_compatibility,
                openapi,
                "alternate-input",
            )
        if dynamic_migration is not None:
            validate_migration(
                dynamic_migration,
                dynamic_inventory,
                dynamic_compatibility,
                migration_schema,
                "alternate-input",
            )
    validate_research()

    if FAILURES:
        print(f"VCF architecture verification failed ({len(FAILURES)} finding(s)):")
        for failure in FAILURES:
            print(f"FAIL: {failure}")
        return 1
    print("VCF architecture verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
