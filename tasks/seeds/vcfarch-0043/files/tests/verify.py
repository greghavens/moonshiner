#!/usr/bin/env python3
"""Protected, offline acceptance verifier for vcfarch-0043.

The committed SddcSpec is intentionally validated first. Research is graded
only as a committed note; compatibility grading uses the pinned snapshot and
the verifier never makes network requests.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SDDC_PATH = ROOT / "deliverables" / "sddc-spec.json"
PLAN_PATH = ROOT / "deliverables" / "migration-plan.json"
RESEARCH_PATH = ROOT / "RESEARCH.md"
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        display_path = path.relative_to(ROOT)
    except ValueError:
        display_path = path
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as error:
        raise VerificationError(f"missing required artifact: {display_path}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid UTF-8 JSON in {display_path}: {error}") from error


def load_text(path: Path) -> str:
    try:
        display_path = path.relative_to(ROOT)
    except ValueError:
        display_path = path
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise VerificationError(f"missing required artifact: {display_path}") from error
    except UnicodeDecodeError as error:
        raise VerificationError(f"invalid UTF-8 text in {display_path}: {error}") from error


def json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise VerificationError(f"only local schema references are allowed, got {pointer!r}")
    current = document
    for encoded in pointer[2:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        try:
            current = current[token]
        except (KeyError, TypeError) as error:
            raise VerificationError(f"unresolved schema reference {pointer!r}") from error
    return current


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
    return True


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the JSON-Schema subset used by the pinned OpenAPI/schema files."""
    errors: list[str] = []
    if "$ref" in schema:
        return validate_schema(value, json_pointer(document, schema["$ref"]), document, path)

    if value is None and schema.get("nullable") is True:
        return errors
    for keyword in ("allOf",):
        for part in schema.get(keyword, []):
            errors.extend(validate_schema(value, part, document, path))
    if "anyOf" in schema:
        branches = [validate_schema(value, branch, document, path) for branch in schema["anyOf"]]
        if not any(not branch_errors for branch_errors in branches):
            errors.append(f"{path}: does not satisfy anyOf")
            return errors
    if "oneOf" in schema:
        branches = [validate_schema(value, branch, document, path) for branch in schema["oneOf"]]
        if sum(not branch_errors for branch_errors in branches) != 1:
            errors.append(f"{path}: does not satisfy exactly one oneOf branch")
            return errors

    expected = schema.get("type")
    if expected:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(type_matches(value, item) for item in expected_types):
            errors.append(f"{path}: expected {' or '.join(expected_types)}, got {type(value).__name__}")
            return errors

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']!r}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}: missing required property {required!r}")
        additional = schema.get("additionalProperties", True)
        if additional is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{path}: additional property {key!r} is not allowed")
        for key, child in value.items():
            if key in properties:
                errors.extend(validate_schema(child, properties[key], document, f"{path}.{key}"))
            elif isinstance(additional, dict):
                errors.extend(validate_schema(child, additional, document, f"{path}.{key}"))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, schema["items"], document, f"{path}[{index}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as error:
                raise VerificationError(f"invalid pinned schema pattern at {path}: {error}") from error
            if matched is None:
                errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def validate_sddc_first() -> tuple[dict[str, Any], dict[str, Any]]:
    """This is deliberately the first acceptance stage, per the task contract."""
    sddc = load_json(SDDC_PATH)
    openapi = load_json(OPENAPI_PATH)
    try:
        schema = openapi["components"]["schemas"]["SddcSpec"]
    except KeyError as error:
        raise VerificationError("pinned installer specification has no SddcSpec schema") from error
    errors = validate_schema(sddc, schema, openapi)
    if errors:
        detail = "\n  - ".join(errors[:30])
        raise VerificationError(f"SddcSpec schema validation failed before architecture checks:\n  - {detail}")
    print("[1/5] SddcSpec validates against the pinned installer OpenAPI schema")
    return sddc, openapi


def validate_architecture(
    sddc: dict[str, Any],
    estate: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    announce: bool = True,
) -> None:
    target = estate["targetDomain"]
    requirements = estate["requirements"]
    services = estate["services"]
    limits = snapshot["designLimits"]
    combo = next(
        (item for item in snapshot["supportedCombinations"] if item["release"] == target["version"]),
        None,
    )
    require(combo is not None, "target release is absent from supported combinations")

    require(sddc["sddcId"] == target["id"], "SddcSpec targets the wrong domain")
    require(sddc.get("workflowType") == limits["supportedWorkflowType"], "wrong installer workflow type")
    require(sddc.get("version") == target["version"] == snapshot["targetRelease"], "wrong VCF target release")
    require(sddc.get("vcfInstanceName") == estate["fleetName"], "SddcSpec targets the wrong fleet")
    require("sddcManagerSpec" not in sddc, "greenfield WLD spec must not alter or redeploy SDDC Manager")

    hosts = sddc.get("hostSpecs", [])
    selected_names = [host.get("hostname") for host in hosts]
    candidates = estate["candidateHosts"]
    expected_names = [host["hostname"] for host in candidates]
    require(len(selected_names) == len(set(selected_names)), "SddcSpec repeats a candidate host")
    require(set(selected_names) == set(expected_names), "SddcSpec must select all candidate hosts")
    require(len(selected_names) - requirements["hostFailuresToTolerate"] >= limits["minimumHostsForFtt2"], "host design cannot sustain N+2 and FTT=2")
    require(all(host["site"] == target["site"] for host in candidates), "selected host is outside target site")
    require(len({host["faultDomain"] for host in candidates}) >= requirements["minimumFaultDomains"], "insufficient fault domains")
    require(all(host["hardwareProfile"] in limits["certifiedHardwareProfiles"] for host in candidates), "uncertified hardware selected")

    survivors = len(candidates) - requirements["hostFailuresToTolerate"]
    reserve_factor = 1.0 - requirements["reservedCapacityPercent"] / 100.0
    ordered_by_name = sorted(candidates, key=lambda item: item["hostname"])
    surviving_hosts = ordered_by_name[:survivors]
    available_vcpu = sum(item["physicalCores"] for item in surviving_hosts) * requirements["maximumCpuOvercommitRatio"] * reserve_factor
    available_memory = sum(item["memoryGiB"] for item in surviving_hosts) * reserve_factor
    available_storage = sum(item["rawStorageTiB"] for item in candidates) * limits["vsanEsaFtt2UsableFactor"] * reserve_factor
    require(available_vcpu >= requirements["workloadVcpu"], "N+2 CPU capacity misses workload plus reserve")
    require(available_memory >= requirements["workloadMemoryGiB"], "N+2 memory capacity misses workload plus reserve")
    require(available_storage >= requirements["workloadUsableStorageTiB"], "FTT=2 storage capacity misses workload plus reserve")

    dns = sddc["dnsSpec"]
    require(dns.get("subdomain") == services["dnsDomain"], "wrong DNS domain")
    require(dns.get("nameservers") == services["dnsServers"], "wrong DNS servers")
    require(sddc.get("ntpServers") == services["ntpServers"], "wrong NTP servers")

    expected_networks = {item["networkType"]: item for item in estate["networks"]}
    actual_networks = {item["networkType"]: item for item in sddc.get("networkSpecs", [])}
    require(len(actual_networks) == len(sddc.get("networkSpecs", [])), "network type repeated in SddcSpec")
    require(actual_networks.keys() == expected_networks.keys(), "network types do not match inventory")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for source_key, output_key in (("vlanId", "vlanId"), ("cidr", "subnet"), ("gateway", "gateway"), ("subnetMask", "subnetMask"), ("mtu", "mtu")):
            require(actual.get(output_key) == expected[source_key], f"{network_type} {output_key} mismatch")
        if "ipAddresses" in expected:
            require(actual.get("includeIpAddress") == expected["ipAddresses"], f"{network_type} IP allocation mismatch")
    require(all(network["mtu"] == limits["supportedMtu"] for network in actual_networks.values()), "unsupported MTU")

    dvs_specs = sddc.get("dvsSpecs", [])
    require(dvs_specs, "architecture must define at least one vSphere Distributed Switch")
    require(all(dvs.get("mtu") == limits["supportedMtu"] for dvs in dvs_specs), "DVS MTU mismatch")
    carried_networks = {
        network_type
        for dvs in dvs_specs
        for network_type in dvs.get("networks", [])
    }
    require(carried_networks == set(expected_networks), "DVS design does not carry every required network")

    datastore = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    require(datastore.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")
    require(datastore.get("failuresToTolerate") == requirements["hostFailuresToTolerate"], "vSAN FTT does not match N+2 requirement")

    vcenter = sddc["vcenterSpec"]
    require(vcenter.get("vcenterHostname") == services["vcenterFqdn"], "wrong vCenter FQDN")
    require(vcenter.get("useExistingDeployment") is False, "target vCenter must be greenfield")
    require(vcenter.get("version") == combo["VCENTER"], "unsupported vCenter target")
    nsx = sddc.get("nsxtSpec", {})
    require(nsx.get("useExistingDeployment") is False, "target NSX must be greenfield")
    require(nsx.get("version") == combo["NSX"], "unsupported NSX target")
    require(nsx.get("vipFqdn") == services["nsxVipFqdn"], "wrong NSX VIP")
    manager_names = [item.get("hostname") for item in nsx.get("nsxtManagers", [])]
    require(len(manager_names) == len(set(manager_names)), "NSX manager repeated")
    require(set(manager_names) == set(services["nsxManagerFqdns"]), "NSX manager set mismatch")
    require(len(manager_names) >= max(requirements["minimumNsxManagers"], limits["minimumNsxManagers"]), "insufficient NSX managers")
    overlay = expected_networks["HOST_OVERLAY"]
    subnets = nsx.get("ipAddressPoolSpec", {}).get("subnets", [])
    require(len(subnets) == 1, "NSX host overlay needs one pinned pool subnet")
    require(subnets[0].get("cidr") == overlay["cidr"] and subnets[0].get("gateway") == overlay["gateway"], "NSX overlay subnet mismatch")
    require(subnets[0].get("ipAddressPoolRanges") == [{"start": overlay["ipPoolStart"], "end": overlay["ipPoolEnd"]}], "NSX overlay pool range mismatch")
    if announce:
        print("[2/5] capacity, availability, site, network, storage, and BOM architecture checks pass")


def validate_migration(
    plan: dict[str, Any],
    estate: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    announce: bool = True,
) -> None:
    plan_schema = load_json(ROOT / "schemas" / "migration-plan.schema.json")
    errors = validate_schema(plan, plan_schema, plan_schema)
    if errors:
        detail = "\n  - ".join(errors[:30])
        raise VerificationError(f"migration-plan schema validation failed:\n  - {detail}")
    require(plan["estateId"] == estate["estateId"], "migration plan estateId mismatch")
    require(plan["targetRelease"] == snapshot["targetRelease"], "migration plan target release mismatch")
    require(plan["targetDomain"] == estate["targetDomain"]["id"], "migration plan target domain mismatch")

    expected_management = {item["componentId"]: item for item in estate["managementDomain"]["components"]}
    actual_management = {item["componentId"]: item for item in plan["managementDomain"]["components"]}
    require(plan["managementDomain"]["action"] == "NO_CHANGE", "management domain action must be NO_CHANGE")
    require(
        len(actual_management) == len(plan["managementDomain"]["components"]),
        "management component repeated",
    )
    require(actual_management.keys() == expected_management.keys(), "every management component must be named once")
    no_change_gate = snapshot["managementDomainNoChangeGate"]
    for component_id, expected in expected_management.items():
        actual = actual_management[component_id]
        require(actual["componentType"] == expected["componentType"], f"management type mismatch for {component_id}")
        require(actual["currentVersion"] == expected["currentVersion"], f"management current version mismatch for {component_id}")
        require(actual["targetVersion"] == expected["currentVersion"], f"management target must remain unchanged for {component_id}")
        require(actual["action"] == "NO_CHANGE", f"management action changes {component_id}")
        require(actual["gates"] == [no_change_gate], f"management no-change gate mismatch for {component_id}")

    expected_scope = {item["componentId"]: item for item in estate["migrationScope"]["components"]}
    steps = plan["steps"]
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "migration order must be contiguous from 1")
    require(len({step["componentId"] for step in steps}) == len(steps), "migration component repeated")
    require({step["componentId"] for step in steps} == expected_scope.keys(), "every legacy component must be planned exactly once")
    require(not ({step["componentId"] for step in steps} & expected_management.keys()), "management component appears in migration steps")

    rules = {
        (item["componentType"], item["fromVersion"]): item
        for item in snapshot["upgradePaths"]
    }
    expected_ranks: list[int] = []
    for step in steps:
        expected = expected_scope[step["componentId"]]
        rule = rules.get((expected["componentType"], expected["currentVersion"]))
        require(rule is not None, f"no pinned upgrade path for {step['componentId']}")
        require(step["componentType"] == expected["componentType"], f"component type mismatch for {step['componentId']}")
        require(step["currentVersion"] == expected["currentVersion"], f"current version mismatch for {step['componentId']}")
        require(step["targetVersion"] == rule["targetVersion"], f"unsupported target for {step['componentId']}")
        require(step["action"] == rule["action"], f"unsupported action for {step['componentId']}")
        require(step["targetDomain"] == estate["targetDomain"]["id"], f"wrong target domain for {step['componentId']}")
        require(
            len(step["gates"]) == len(set(step["gates"]))
            and set(step["gates"]) == set(rule["requiredGates"]),
            f"gate mismatch for {step['componentId']}",
        )
        expected_ranks.append(rule["orderRank"])
    require(expected_ranks == sorted(expected_ranks), "migration steps violate pinned NSX -> vCenter -> ESXi order")
    if announce:
        print("[3/5] migration schema, full inventory coverage, targets, gates, and ordering pass")


def validate_research() -> None:
    research = load_text(RESEARCH_PATH)
    require(
        re.search(r"(?i)access(?:ed|\s+date|ed\s+on)?[^\r\n]{0,20}\d{4}-\d{2}-\d{2}\b", research)
        is not None,
        "RESEARCH.md must record an ISO access date",
    )
    url_pattern = re.compile(
        r"https://(?:[a-z0-9-]+\.)*broadcom\.com/[^\s)>]+",
        re.IGNORECASE,
    )
    url_matches = list(url_pattern.finditer(research))
    urls = [match.group(0).rstrip(".,;") for match in url_matches]
    require(len(urls) >= 2, "RESEARCH.md must cite Broadcom sources for both research topics")
    require(len(set(urls)) == len(urls), "RESEARCH.md contains a duplicate source URL")
    for match in url_matches:
        line_start = research.rfind("\n", 0, match.start()) + 1
        line_end = research.find("\n", match.end())
        if line_end == -1:
            line_end = len(research)
        source_line_without_url = research[line_start:line_end].replace(match.group(0), "")
        require(
            re.search(r"[A-Za-z][A-Za-z0-9 /&:()._-]{4,}", source_line_without_url) is not None,
            "each Broadcom source URL must be accompanied by its page title",
        )
    lowered = research.lower()
    require(".invalid" not in lowered and "localhost" not in lowered, "RESEARCH.md contains a fixture URL")
    require(
        "9.0" in lowered and all(component in lowered for component in ("nsx", "vcenter", "esx")),
        "RESEARCH.md must discuss the supported NSX, vCenter, and ESX target combination",
    )
    require(
        any(marker in lowered for marker in ("order", "sequence", " before ", " after ", " then ", "->")),
        "RESEARCH.md must record an upgrade order conclusion",
    )
    require(
        any(marker in lowered for marker in ("used", "conclusion", "confirmed", "determined", "applied")),
        "RESEARCH.md must identify conclusions used in the design",
    )
    print("[4/5] live-source titles, URLs, access date, target combination, and upgrade-order notes are recorded")


def validate_module_and_regeneration(
    committed_sddc: dict[str, Any],
    committed_plan: dict[str, Any],
    openapi: dict[str, Any],
    plan_schema: dict[str, Any],
    estate: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    module_dir = ROOT / "VcfGreenfieldArchitecture"
    manifest = module_dir / "VcfGreenfieldArchitecture.psd1"
    implementation = module_dir / "VcfGreenfieldArchitecture.psm1"
    require(manifest.is_file() and implementation.is_file(), "PowerShell module manifest/implementation missing")
    implementation.read_text(encoding="utf-8")
    inspection_command = (
        "$ErrorActionPreference='Stop'; $tokens=$null; $parseErrors=$null; "
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile('{implementation.as_posix()}', "
        "[ref]$tokens, [ref]$parseErrors); "
        f"$manifestData=Import-PowerShellDataFile -LiteralPath '{manifest.as_posix()}'; "
        "$requiredNames=@($manifestData.RequiredModules | ForEach-Object { "
        "if ($_ -is [string]) { $_ } else { $_.ModuleName } }); "
        "$commands=@($ast.FindAll({ param($node) "
        "$node -is [System.Management.Automation.Language.CommandAst] }, $true) | "
        "ForEach-Object { $_.GetCommandName() } | Where-Object { $_ }); "
        "$functions=@($ast.FindAll({ param($node) "
        "$node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | "
        "ForEach-Object { $_.Name }); "
        "[ordered]@{ parseErrors=@($parseErrors | ForEach-Object { $_.Message }); "
        "requiredModules=$requiredNames; commands=$commands; functions=$functions } | "
        "ConvertTo-Json -Depth 4 -Compress"
    )
    try:
        inspection_result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", inspection_command],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as error:
        raise VerificationError("pwsh prerequisite is not installed") from error
    except subprocess.TimeoutExpired as error:
        raise VerificationError("PowerShell source inspection timed out") from error
    require(
        inspection_result.returncode == 0,
        f"PowerShell source inspection failed:\n{inspection_result.stdout}\n{inspection_result.stderr}",
    )
    try:
        inspection = json.loads(inspection_result.stdout)
    except json.JSONDecodeError as error:
        raise VerificationError("PowerShell source inspection returned invalid JSON") from error
    require(not inspection["parseErrors"], f"PowerShell module has parse errors: {inspection['parseErrors']}")
    required_modules = {str(name).casefold() for name in inspection["requiredModules"]}
    require(
        "vmware.sdk.vcf.installer" in required_modules,
        "module manifest must require VMware.Sdk.Vcf.Installer",
    )
    invoked_commands = {str(name).casefold() for name in inspection["commands"]}
    defined_functions = {str(name).casefold() for name in inspection["functions"]}
    for command in (
        "Initialize-VcfInstallerSddcSpec",
        "Initialize-VcfInstallerSddcHostSpec",
        "Initialize-VcfInstallerSddcVcenterSpec",
        "Initialize-VcfInstallerSddcNsxtSpec",
        "Initialize-VcfInstallerSddcNetworkSpec",
    ):
        normalized = command.casefold()
        require(normalized in invoked_commands, f"module does not invoke required SDK model cmdlet {command}")
        require(normalized not in defined_functions, f"module replaces required SDK model cmdlet {command}")
    for forbidden in ("Connect-VcfInstallerServer", "Invoke-VcfInstallerDeploySddc", "Invoke-RestMethod", "Invoke-WebRequest"):
        require(forbidden.casefold() not in invoked_commands, f"architecture module must not perform external action: {forbidden}")

    def run_export(estate_path: Path, compatibility_path: Path, output: Path) -> None:
        command = (
            "$ErrorActionPreference='Stop'; "
            f"Import-Module '{manifest.as_posix()}' -Force; "
            f"Export-VcfArchitecture -EstatePath '{estate_path.as_posix()}' "
            f"-CompatibilityPath '{compatibility_path.as_posix()}' "
            f"-OutputDirectory '{output.as_posix()}'"
        )
        try:
            result = subprocess.run(
                ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=90,
                check=False,
            )
        except FileNotFoundError as error:
            raise VerificationError("pwsh prerequisite is not installed") from error
        except subprocess.TimeoutExpired as error:
            raise VerificationError("PowerShell module regeneration timed out") from error
        require(result.returncode == 0, f"PowerShell module failed regeneration:\n{result.stdout}\n{result.stderr}")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        workspace = Path(temporary)
        default_output = workspace / "default-output"
        run_export(
            ROOT / "fixtures" / "estate.json",
            ROOT / "fixtures" / "compatibility-snapshot.json",
            default_output,
        )
        generated_sddc = load_json(default_output / "sddc-spec.json")
        generated_plan = load_json(default_output / "migration-plan.json")
        require(not validate_schema(generated_sddc, openapi["components"]["schemas"]["SddcSpec"], openapi), "module generated a schema-invalid SddcSpec")
        require(not validate_schema(generated_plan, plan_schema, plan_schema), "module generated a schema-invalid migration plan")
        require(generated_sddc == committed_sddc, "committed SddcSpec is not reproducible by the module")
        require(generated_plan == committed_plan, "committed migration plan is not reproducible by the module")

        # A second, deterministic input set proves that all three command parameters
        # are functional and that the artifacts are derived rather than copied.
        variant_estate = json.loads(json.dumps(estate))
        variant_snapshot = json.loads(json.dumps(snapshot))
        variant_estate["estateId"] = "acme-dal01-estate-variant"
        variant_estate["fleetName"] = "acme-vcf-fleet-variant"
        variant_estate["targetDomain"]["id"] = "dal01-w03"
        variant_estate["services"]["vcenterFqdn"] = "dal01-w03-vc01.corp.example"
        variant_estate["services"]["nsxVipFqdn"] = "dal01-w03-nsx.corp.example"
        variant_estate["services"]["nsxManagerFqdns"] = [
            f"dal01-w03-nsx0{index}.corp.example" for index in range(1, 4)
        ]
        for host in variant_estate["candidateHosts"]:
            host["hostname"] = host["hostname"].replace("w02", "w03")
        for component in variant_estate["migrationScope"]["components"]:
            component["componentId"] = component["componentId"].replace("w01", "w07")

        variant_snapshot["managementDomainNoChangeGate"] = "variant-management-domain-no-change"
        variant_combo = variant_snapshot["supportedCombinations"][0]
        variant_combo["NSX"] = "9.0.0.0.24733064"
        variant_combo["VCENTER"] = "9.0.0.0.24755231"
        variant_combo["ESXI"] = "9.0.0-24755230"
        for rule in variant_snapshot["upgradePaths"]:
            rule["targetVersion"] = variant_combo[rule["componentType"]]
            rule["requiredGates"] = [f"variant-{gate}" for gate in rule["requiredGates"]]

        variant_input = workspace / "variant-input"
        variant_input.mkdir()
        variant_estate_path = variant_input / "estate.json"
        variant_snapshot_path = variant_input / "compatibility.json"
        variant_estate_path.write_text(json.dumps(variant_estate, indent=2) + "\n", encoding="utf-8")
        variant_snapshot_path.write_text(json.dumps(variant_snapshot, indent=2) + "\n", encoding="utf-8")
        variant_output = workspace / "variant-output"
        run_export(variant_estate_path, variant_snapshot_path, variant_output)
        variant_sddc = load_json(variant_output / "sddc-spec.json")
        variant_plan = load_json(variant_output / "migration-plan.json")
        require(not validate_schema(variant_sddc, openapi["components"]["schemas"]["SddcSpec"], openapi), "module generated a schema-invalid variant SddcSpec")
        require(not validate_schema(variant_plan, plan_schema, plan_schema), "module generated a schema-invalid variant migration plan")
        validate_architecture(variant_sddc, variant_estate, variant_snapshot, announce=False)
        validate_migration(variant_plan, variant_estate, variant_snapshot, announce=False)
        require(variant_sddc != committed_sddc, "module ignored changed estate/compatibility inputs")
        require(variant_plan != committed_plan, "module ignored changed estate/compatibility inputs")
    print("[5/5] real VMware SDK-backed module reproducibly derives artifacts from both input files")


def main() -> int:
    try:
        # No fixture, snapshot, migration, or module checks occur before this call.
        sddc, openapi = validate_sddc_first()

        estate = load_json(ROOT / "fixtures" / "estate.json")
        snapshot = load_json(ROOT / "fixtures" / "compatibility-snapshot.json")
        plan = load_json(PLAN_PATH)
        validate_architecture(sddc, estate, snapshot)
        validate_migration(plan, estate, snapshot)
        validate_research()
        plan_schema = load_json(ROOT / "schemas" / "migration-plan.schema.json")
        validate_module_and_regeneration(sddc, plan, openapi, plan_schema, estate, snapshot)
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.0 greenfield workload-domain architecture is complete and deterministic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
