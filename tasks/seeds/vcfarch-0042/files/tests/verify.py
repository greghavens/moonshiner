#!/usr/bin/env python3
"""Protected deterministic verifier for vcfarch-0042.

This verifier is intentionally offline.  Live research is a trace requirement;
the generated architecture is graded only against protected local authorities.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
SDDC_PATH = ROOT / "output/sddc-spec.json"
MIGRATION_PATH = ROOT / "output/migration-plan.json"
RESEARCH_PATH = ROOT / "output/research-sources.json"


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def resolve_ref(document: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        fail(f"unsupported non-local schema reference: {ref}")
    node = document
    for raw in ref[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        try:
            node = node[key]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {ref}")
    return node


def type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    fail(f"unsupported JSON Schema type: {expected}")


def schema_errors(
    value: Any,
    schema: Any,
    document: Any,
    path: str = "$",
) -> list[str]:
    """Validate the JSON Schema keywords used by the pinned OpenAPI/schema files."""
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: rejected by false schema"]
    if not isinstance(schema, dict):
        return [f"{path}: invalid schema node"]
    if "$ref" in schema:
        return schema_errors(value, resolve_ref(document, schema["$ref"]), document, path)
    if value is None and schema.get("nullable") is True:
        return []

    errors: list[str] = []
    if "allOf" in schema:
        for branch in schema["allOf"]:
            errors.extend(schema_errors(value, branch, document, path))
    if "anyOf" in schema:
        if not any(not schema_errors(value, branch, document, path) for branch in schema["anyOf"]):
            errors.append(f"{path}: does not match anyOf")
    if "oneOf" in schema:
        matches = sum(not schema_errors(value, branch, document, path) for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: matches {matches} oneOf branches, expected exactly one")
    if "not" in schema and not schema_errors(value, schema["not"], document, path):
        errors.append(f"{path}: matches forbidden schema")

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is not in enum")

    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(type_matches(value, item) for item in allowed):
            errors.append(f"{path}: expected type {expected}, got {type(value).__name__}")
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
                errors.extend(schema_errors(item, properties[key], document, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    schema_errors(item, schema["additionalProperties"], document, child_path)
                )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{path}: too many properties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items are not unique")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, schema["items"], document, f"{path}[{index}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
            except re.error as exc:
                fail(f"invalid protected schema regex {schema['pattern']!r}: {exc}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: not below exclusiveMaximum")

    return errors


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        fail(f"{message}: expected {expected!r}, got {actual!r}")


def index_unique(items: list[dict[str, Any]], key: str, label: str) -> dict[Any, dict[str, Any]]:
    result: dict[Any, dict[str, Any]] = {}
    for item in items:
        if key not in item:
            fail(f"{label} entry is missing {key!r}")
        if item[key] in result:
            fail(f"duplicate {label} {key}: {item[key]!r}")
        result[item[key]] = item
    return result


def verify_capacity(requirements: dict[str, Any]) -> None:
    hosts = requirements["hosts"]
    reserve = requirements["availability"]["maintenanceReserveHosts"]
    if reserve != requirements["availability"]["hostFailuresToTolerate"]:
        fail("the supplied availability reserve is internally inconsistent")
    survivors = len(hosts) - reserve
    if survivors <= 0:
        fail("the supplied availability reserve leaves no hosts")
    factors = requirements["capacityFactors"]
    # Capacity after the largest hosts are unavailable is the conservative N+1 value.
    core_capacity = sum(sorted(h["physicalCores"] for h in hosts)[:survivors])
    memory_capacity = sum(sorted(h["memoryGiB"] for h in hosts)[:survivors])
    storage_capacity = sum(sorted(h["rawStorageTiB"] for h in hosts)[:survivors])
    usable = {
        "workloadVcpu": core_capacity * factors["vcpuPerPhysicalCore"],
        "workloadMemoryGiB": memory_capacity * factors["memoryUsableFraction"],
        "workloadUsableStorageTiB": storage_capacity * factors["storageUsableFraction"],
    }
    for key, demand in requirements["demand"].items():
        if usable[key] + 1e-9 < demand:
            fail(f"fixture's N+1 {key} capacity does not meet demand")


def verify_sddc_semantics(
    sddc: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    deployment = requirements["deployment"]
    authority = snapshot["greenfield"]
    design = authority["supportedDesign"]
    bom = authority["billOfMaterials"]

    assert_equal(deployment["targetVcfVersion"], authority["targetVcfVersion"], "target release")
    assert_equal(deployment["topology"], design["topology"], "topology")
    assert_equal(deployment["siteCount"], design["siteCount"], "site count")
    assert_equal(len(deployment["sites"]), design["siteCount"], "listed site count")
    assert_equal(len(requirements["hosts"]), design["hostCount"], "fixture host count")
    assert_equal(design["hostCount"], design["minimumSupportedHostCount"], "minimum topology")
    assert_equal(requirements["availability"]["stretchedCluster"], False, "single-site stretch flag")
    assert_equal(
        requirements["availability"]["hostFailuresToTolerate"],
        design["hostFailuresToTolerate"],
        "failure tolerance",
    )
    verify_capacity(requirements)

    assert_equal(sddc.get("sddcId"), deployment["sddcId"], "SddcSpec sddcId")
    assert_equal(sddc.get("workflowType"), deployment["workflowType"], "SddcSpec workflow")
    assert_equal(sddc.get("version"), authority["targetVcfVersion"], "SddcSpec version")
    assert_equal(sddc.get("vcfInstanceName"), deployment["vcfInstanceName"], "VCF instance")
    assert_equal(
        sddc.get("managementPoolName"), deployment["managementPoolName"], "management pool"
    )
    assert_equal(sddc.get("skipEsxThumbprintValidation"), False, "ESX thumbprint validation")
    assert_equal(sddc.get("skipGatewayPingValidation"), False, "gateway validation")

    expected_hosts = [host["hostname"] for host in requirements["hosts"]]
    host_specs = sddc.get("hostSpecs", [])
    assert_equal(len(host_specs), design["hostCount"], "SddcSpec host count")
    assert_equal([host.get("hostname") for host in host_specs], expected_hosts, "host ordering")
    expected_esxi_password = requirements["secretPlaceholders"]["esxiRootPassword"]
    for host in host_specs:
        assert_equal(host.get("credentials", {}).get("username"), "root", "ESX username")
        assert_equal(
            host.get("credentials", {}).get("password"), expected_esxi_password, "ESX placeholder"
        )

    expected_networks = index_unique(requirements["networks"], "networkType", "fixture network")
    actual_networks = index_unique(sddc.get("networkSpecs", []), "networkType", "SddcSpec network")
    assert_equal(set(actual_networks), set(design["requiredNetworkTypes"]), "network types")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for key in (
            "vlanId",
            "subnet",
            "subnetMask",
            "gateway",
            "mtu",
            "includeIpAddress",
        ):
            assert_equal(actual.get(key), expected[key], f"{network_type} {key}")

    assert_equal(sddc.get("dnsSpec"), requirements["dns"], "DNS design")
    assert_equal(sddc.get("ntpServers"), requirements["ntpServers"], "NTP design")

    vcenter = sddc.get("vcenterSpec", {})
    assert_equal(vcenter.get("vcenterHostname"), requirements["names"]["vcenterFqdn"], "vCenter FQDN")
    assert_equal(vcenter.get("version"), bom["VCENTER"], "vCenter target")
    assert_equal(vcenter.get("useExistingDeployment"), False, "greenfield vCenter")
    assert_equal(
        vcenter.get("rootVcenterPassword"),
        requirements["secretPlaceholders"]["vcenterRootPassword"],
        "vCenter placeholder",
    )

    manager = sddc.get("sddcManagerSpec", {})
    assert_equal(manager.get("hostname"), requirements["names"]["sddcManagerFqdn"], "SDDC Manager FQDN")
    assert_equal(manager.get("version"), bom["SDDC_MANAGER_VCF"], "SDDC Manager target")
    assert_equal(manager.get("useExistingDeployment"), False, "greenfield SDDC Manager")
    for field, fixture_field in (
        ("rootPassword", "sddcManagerRootPassword"),
        ("sshPassword", "sddcManagerSshPassword"),
        ("localUserPassword", "sddcManagerLocalPassword"),
    ):
        assert_equal(manager.get(field), requirements["secretPlaceholders"][fixture_field], field)

    nsx = sddc.get("nsxtSpec", {})
    assert_equal(nsx.get("version"), bom["NSX_T_MANAGER"], "NSX target")
    assert_equal(nsx.get("useExistingDeployment"), False, "greenfield NSX")
    assert_equal(nsx.get("vipFqdn"), requirements["names"]["nsxVipFqdn"], "NSX VIP")
    assert_equal(nsx.get("transportVlanId"), requirements["nsx"]["transportVlanId"], "NSX VLAN")
    assert_equal(nsx.get("nsxtManagerSize"), requirements["nsx"]["managerSize"], "NSX size")
    for field, fixture_field in (
        ("rootNsxtManagerPassword", "nsxRootPassword"),
        ("nsxtAdminPassword", "nsxAdminPassword"),
        ("nsxtAuditPassword", "nsxAuditPassword"),
    ):
        assert_equal(nsx.get(field), requirements["secretPlaceholders"][fixture_field], field)
    manager_names = [item.get("hostname") for item in nsx.get("nsxtManagers", [])]
    assert_equal(manager_names, requirements["names"]["nsxManagerFqdns"], "NSX manager nodes")
    assert_equal(len(manager_names), design["nsxManagerCount"], "NSX manager count")

    cluster = sddc.get("clusterSpec", {})
    assert_equal(cluster.get("datacenterName"), requirements["names"]["datacenter"], "datacenter")
    assert_equal(cluster.get("clusterName"), requirements["names"]["cluster"], "cluster")
    pools = index_unique(cluster.get("resourcePoolSpecs", []), "type", "resource pool")
    expected_pools = index_unique(requirements["resourcePools"], "type", "fixture resource pool")
    assert_equal(set(pools), set(expected_pools), "resource pool types")
    for pool_type, expected in expected_pools.items():
        for key in ("name", "cpuReservationPercentage", "memoryReservationPercentage"):
            assert_equal(pools[pool_type].get(key), expected[key], f"{pool_type} pool {key}")

    datastore = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    assert_equal(requirements["storage"]["type"], design["storageType"], "storage type")
    assert_equal(datastore.get("datastoreName"), requirements["names"]["datastore"], "datastore")
    assert_equal(datastore.get("esaConfig", {}).get("enabled"), True, "vSAN ESA")
    assert_equal(
        datastore.get("failuresToTolerate"),
        requirements["storage"]["failuresToTolerate"],
        "vSAN FTT",
    )

    dvs_specs = sddc.get("dvsSpecs", [])
    assert_equal(len(dvs_specs), 1, "distributed switch count")
    dvs = dvs_specs[0]
    assert_equal(dvs.get("dvsName"), requirements["names"]["distributedSwitch"], "DVS name")
    assert_equal(dvs.get("networks"), design["requiredNetworkTypes"], "DVS network types")
    assert_equal(dvs.get("mtu"), 9000, "DVS MTU")
    assert_equal(dvs.get("vmnicsToUplinks"), design["uplinks"], "DVS uplinks")


def verify_migration(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    authority = snapshot["migration"]
    assert_equal(plan.get("schemaVersion"), "1.0", "migration schema version")
    assert_equal(plan.get("inventoryId"), inventory["inventoryId"], "migration inventory")
    assert_equal(plan.get("inventoryId"), authority["inventoryId"], "authority inventory")
    assert_equal(plan.get("sourceVcfVersion"), inventory["sourceVcfVersion"], "source VCF")
    assert_equal(plan.get("sourceVcfVersion"), authority["sourceVcfVersion"], "authority source")
    assert_equal(plan.get("targetVcfVersion"), authority["targetVcfVersion"], "migration target")

    inventory_by_id = index_unique(inventory["components"], "id", "inventory component")
    steps = plan.get("steps", [])
    steps_by_id = index_unique(steps, "componentId", "migration component")
    expected_order = authority["orderedComponentIds"]
    assert_equal(set(steps_by_id), set(inventory_by_id), "migration inventory coverage")
    assert_equal([step["componentId"] for step in steps], expected_order, "migration order")
    assert_equal([step["order"] for step in steps], list(range(1, len(steps) + 1)), "step numbers")

    completed: set[str] = set()
    for step in steps:
        component_id = step["componentId"]
        source = inventory_by_id[component_id]
        assert_equal(step["component"], source["name"], f"{component_id} name")
        assert_equal(step["componentType"], source["type"], f"{component_id} type")
        assert_equal(step["fromVersion"], source["version"], f"{component_id} source version")
        assert_equal(
            step["targetVersion"], authority["targets"][component_id], f"{component_id} target"
        )
        assert_equal(step["gates"], authority["gates"][component_id], f"{component_id} gates")
        for gate in step["gates"]:
            unmet = set(gate["requiresCompleted"]) - completed
            if unmet:
                fail(f"{component_id} gate {gate['id']} depends on later/unlisted steps: {sorted(unmet)}")
        completed.add(component_id)


def verify_research_record(record: Any) -> None:
    if not isinstance(record, dict):
        fail("research-sources.json must contain a JSON object")

    researched_at = record.get("researchedAt")
    if not isinstance(researched_at, str) or not researched_at.endswith("Z"):
        fail("research-sources researchedAt must be a UTC timestamp ending in Z")
    try:
        parsed_time = datetime.fromisoformat(researched_at[:-1] + "+00:00")
    except ValueError:
        fail("research-sources researchedAt is not a valid ISO 8601 timestamp")
    if parsed_time.tzinfo is None or parsed_time.utcoffset() != timezone.utc.utcoffset(parsed_time):
        fail("research-sources researchedAt must be in UTC")

    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        fail("research-sources must record at least one consulted source")

    broadcom_publication = False
    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        if not isinstance(source, dict):
            fail(f"{label} must be an object")
        for field in ("title", "url", "publisher", "claimUsed"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                fail(f"{label} requires a non-empty {field}")

        parsed_url = urlparse(source["url"])
        if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
            fail(f"{label} URL must be an absolute HTTP(S) URL")
        hostname = parsed_url.hostname.rstrip(".").lower()
        if hostname in ("localhost",) or hostname.endswith(
            (".localhost", ".invalid", ".test", ".example")
        ):
            fail(f"{label} URL uses a non-public hostname")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            fail(f"{label} URL uses a non-public IP address")
        if hostname == "broadcom.com" or hostname.endswith(".broadcom.com"):
            broadcom_publication = True
        if hostname == "vmware.com" or hostname.endswith(".vmware.com"):
            broadcom_publication = True

    if not broadcom_publication:
        fail("research-sources must include a Broadcom-published source")


def powershell_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def verify_module(sddc: Any, migration: Any) -> None:
    manifest = ROOT / "VcfArchitecture/VcfArchitecture.psd1"
    module = ROOT / "VcfArchitecture/VcfArchitecture.psm1"
    if not manifest.is_file() or not module.is_file():
        fail("PowerShell module manifest and implementation are required")

    pwsh = shutil.which("pwsh")
    if pwsh is None:
        fail("pwsh is required by this PowerShell task")
    parse_script = (
        "$e=$null;$t=$null;"
        f"$a=[System.Management.Automation.Language.Parser]::ParseFile({powershell_quote(module)},[ref]$t,[ref]$e);"
        "if($e.Count){$e|ForEach-Object{$_.ToString()};exit 1};"
        "$a.FindAll({param($n)$n -is [System.Management.Automation.Language.CommandAst]},$true)|"
        "ForEach-Object{$_.GetCommandName()}|Where-Object{$_}|Sort-Object -Unique|"
        "ConvertTo-Json -Compress -AsArray"
    )
    parsed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", parse_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if parsed.returncode != 0:
        fail(f"PowerShell parser rejected VcfArchitecture.psm1: {parsed.stdout}{parsed.stderr}")
    try:
        parsed_commands = set(json.loads(parsed.stdout))
    except json.JSONDecodeError as exc:
        fail(f"could not inspect PowerShell commands: {exc}")
    for sdk_command in (
        "Initialize-VcfInstallerSddcSpec",
        "Initialize-VcfInstallerSddcHostSpec",
        "Initialize-VcfInstallerSddcNetworkSpec",
        "Initialize-VcfInstallerSddcVcenterSpec",
        "Initialize-VcfInstallerSddcNsxtSpec",
        "Initialize-VcfInstallerSddcDatastoreSpec",
    ):
        if sdk_command not in parsed_commands:
            fail(f"module does not construct the installer model with {sdk_command}")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_name:
        temp = Path(temp_name)
        generated_sddc = temp / "sddc.json"
        generated_migration = temp / "migration.json"
        command = ";".join(
            [
                "$ErrorActionPreference='Stop'",
                f"Import-Module {powershell_quote(manifest)} -Force",
                (
                    f"New-VcfGreenfieldSddcSpec -RequirementsPath {powershell_quote(ROOT / 'fixtures/design-requirements.json')} "
                    f"-CompatibilityPath {powershell_quote(ROOT / 'compatibility/compatibility-snapshot.json')} "
                    f"-OutputPath {powershell_quote(generated_sddc)} | Out-Null"
                ),
                (
                    f"New-VcfEstateMigrationPlan -InventoryPath {powershell_quote(ROOT / 'fixtures/estate-inventory.json')} "
                    f"-CompatibilityPath {powershell_quote(ROOT / 'compatibility/compatibility-snapshot.json')} "
                    f"-OutputPath {powershell_quote(generated_migration)} | Out-Null"
                ),
            ]
        )
        environment = os.environ.copy()
        # Local author validation may provide a temporary module path; the seed itself does not.
        run = subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if run.returncode != 0:
            fail(f"PowerShell module execution failed: {run.stdout}{run.stderr}")
        assert_equal(load_json(generated_sddc), sddc, "module-generated SddcSpec")
        assert_equal(load_json(generated_migration), migration, "module-generated migration plan")


def verify_protected_authorities(snapshot: dict[str, Any]) -> None:
    source_pin = load_json(ROOT / "specifications/vcf-installer/source-pin.json")
    digest = hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()
    assert_equal(digest, source_pin["sha256"], "installer specification digest")
    assert_equal(digest, snapshot["installerSpec"]["sha256"], "snapshot specification digest")
    spec = load_json(SPEC_PATH)
    assert_equal(spec.get("info", {}).get("version"), "9.0.0.0", "installer spec version")
    assert_equal(source_pin["tag"], "9.0.0.0", "installer spec tag")


def main() -> int:
    # Binding requirement: validate the greenfield artifact against the installer
    # specification's own SddcSpec schema before performing any other check.
    openapi = load_json(SPEC_PATH)
    sddc = load_json(SDDC_PATH)
    errors = schema_errors(sddc, {"$ref": "#/components/schemas/SddcSpec"}, openapi)
    if errors:
        fail("SddcSpec schema validation failed:\n  " + "\n  ".join(errors))

    migration_schema = load_json(ROOT / "schemas/migration-plan.schema.json")
    migration = load_json(MIGRATION_PATH)
    migration_errors = schema_errors(migration, migration_schema, migration_schema)
    if migration_errors:
        fail("migration-plan schema validation failed:\n  " + "\n  ".join(migration_errors))

    research = load_json(RESEARCH_PATH)
    verify_research_record(research)

    requirements = load_json(ROOT / "fixtures/design-requirements.json")
    inventory = load_json(ROOT / "fixtures/estate-inventory.json")
    snapshot = load_json(ROOT / "compatibility/compatibility-snapshot.json")
    verify_protected_authorities(snapshot)
    verify_sddc_semantics(sddc, requirements, snapshot)
    verify_migration(migration, inventory, snapshot)
    verify_module(sddc, migration)
    print(
        "PASS: SddcSpec schema, VCF architecture, migration plan, research record, "
        "and PowerShell module"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
