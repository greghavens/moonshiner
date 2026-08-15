#!/usr/bin/env python3
"""Protected, offline acceptance verifier for the VCF migration architecture."""

from __future__ import annotations

from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "output" / "migration-plan.json"
INSTALLER_SCHEMA = ROOT / "schemas" / "vcf-installer-openapi.json"
PLAN_SCHEMA = ROOT / "schemas" / "migration-plan.schema.json"
RESEARCH_NOTES = ROOT / "research-notes.md"


class VerificationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def json_pointer(document: Any, reference: str) -> Any:
    if not reference.startswith("#/"):
        fail(f"unsupported non-local schema reference: {reference}")
    value = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[part]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {reference}")
    return value


def is_json_type(value: Any, expected: str) -> bool:
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
    fail(f"unsupported JSON Schema type in protected verifier: {expected}")


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    schema_root: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_json_schema(value, json_pointer(schema_root, schema["$ref"]), schema_root, path)
        return

    if "allOf" in schema:
        for branch in schema["allOf"]:
            validate_json_schema(value, branch, schema_root, path)
    if "anyOf" in schema:
        branch_errors = []
        for branch in schema["anyOf"]:
            try:
                validate_json_schema(value, branch, schema_root, path)
                break
            except VerificationError as exc:
                branch_errors.append(str(exc))
        else:
            fail(f"{path}: no anyOf branch matched ({'; '.join(branch_errors)})")
    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                validate_json_schema(value, branch, schema_root, path)
                matches += 1
            except VerificationError:
                pass
        if matches != 1:
            fail(f"{path}: expected exactly one oneOf match, got {matches}")

    if "const" in schema and value != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        fail(f"{path}: {value!r} is not one of {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(is_json_type(value, item) for item in expected_types):
            fail(f"{path}: expected type {expected_type!r}, got {type(value).__name__}")

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                fail(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                validate_json_schema(child, properties[name], schema_root, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_json_schema(
                    child, schema["additionalProperties"], schema_root, f"{path}.{name}"
                )

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            fail(f"{path}: expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            fail(f"{path}: expected at most {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: array items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, child in enumerate(value):
                validate_json_schema(child, schema["items"], schema_root, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            fail(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            fail(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                fail(f"invalid pinned schema pattern at {path}: {exc}")
            if matched is None:
                fail(f"{path}: {value!r} does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            fail(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            fail(f"{path}: {value} is above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            fail(f"{path}: {value} is not above {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            fail(f"{path}: {value} is not below {schema['exclusiveMaximum']}")


def ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def run_module(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script = "; ".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$ProgressPreference = 'SilentlyContinue'",
            f"Import-Module {ps_quote(ROOT / 'src' / 'VcfArchitecture.psd1')} -Force",
            (
                "New-VcfMigrationArchitecture "
                f"-InventoryPath {ps_quote(ROOT / 'data' / 'estate-inventory.json')} "
                f"-CompatibilitySnapshotPath {ps_quote(ROOT / 'data' / 'compatibility-snapshot.json')} "
                f"-OutputPath {ps_quote(output_path)}"
            ),
        ]
    )
    environment = os.environ.copy()
    environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    completed = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        fail(f"PowerShell architecture generation failed: {details}")


def expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def index_unique(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        identity = item[key]
        if identity in result:
            fail(f"duplicate {label} {identity!r}")
        result[identity] = item
    return result


def verify_research_notes() -> None:
    try:
        text = RESEARCH_NOTES.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("missing required live-research record: research-notes.md")

    if not text.strip():
        fail("research-notes.md is empty")

    record_start = re.compile(r"(?=^-\s+Title\s*:)", flags=re.IGNORECASE | re.MULTILINE)
    records = [part for part in record_start.split(text) if record_start.match(part)]
    if len(records) < 4:
        fail("research-notes.md must contain one source record for each requested live source kind")

    def record_field(record: str, name: str) -> str:
        match = re.search(
            rf"^\s*(?:-\s*)?{re.escape(name)}\s*:\s*(\S.*)$",
            record,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if match is None:
            fail(f"research source record is missing its {name} field")
        return match.group(1).strip()

    urls: list[str] = []
    allowed_publishers = ("broadcom.com", "vmware.com")
    for record in records:
        title = record_field(record, "Title")
        publisher = record_field(record, "Publisher")
        url_field = record_field(record, "URL")
        accessed = record_field(record, "Accessed")
        claim = record_field(record, "Claim")

        if len(title) < 3:
            fail("research source title is empty")
        if re.search(r"\b(?:Broadcom|VMware)\b", publisher, flags=re.IGNORECASE) is None:
            fail(f"research source publisher is not Broadcom: {publisher}")
        if len(claim) < 20:
            fail(f"research source claim is not substantive: {title}")
        try:
            date.fromisoformat(accessed)
        except ValueError:
            fail(f"invalid research access date: {accessed}")

        field_urls = re.findall(r"https://[^\s<>\]\[()\"']+", url_field)
        if len(field_urls) != 1 or field_urls[0].rstrip(".,;:") != url_field:
            fail(f"research source URL field must contain one exact HTTPS URL: {url_field}")
        url = field_urls[0]
        urls.append(url)
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host:
            fail(f"research source is not an absolute HTTPS URL: {url}")
        if not any(host == suffix or host.endswith("." + suffix) for suffix in allowed_publishers):
            fail(f"research source is not Broadcom-published: {url}")
        if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".invalid"):
            fail(f"research source is not live and reachable: {url}")

    if len(urls) != len(set(urls)):
        fail("research source URLs must be distinct")
    all_urls = [url.rstrip(".,;:") for url in re.findall(r"https://[^\s<>\]\[()\"']+", text)]
    if sorted(all_urls) != sorted(urls):
        fail("every URL in research-notes.md must belong to a complete source record")

    required_source_kinds = {
        "compatibility": r"compatib",
        "interoperability": r"interoperab",
        "release notes": r"release[ -]?notes?",
        "upgrade": r"upgrad",
    }
    for label, pattern in required_source_kinds.items():
        if re.search(pattern, text, flags=re.IGNORECASE) is None:
            fail(f"research-notes.md is missing a live {label} source")


def verify_installer_spec(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    target = inventory["targetVersion"]
    expect(artifact["sddcId"], inventory["sddcId"], "SddcSpec.sddcId")
    expect(artifact["workflowType"], snapshot["targetSpec"]["workflowType"], "SddcSpec.workflowType")
    expect(artifact["version"], target, "SddcSpec.version")
    expect(artifact["vcfInstanceName"], inventory["vcfInstanceName"], "SddcSpec.vcfInstanceName")

    vcenter = artifact["vcenterSpec"]
    expect(vcenter["vcenterHostname"], inventory["managementVcenter"]["fqdn"], "vCenter FQDN")
    expect(vcenter["version"], target, "vCenter target")
    expect(vcenter["useExistingDeployment"], True, "vCenter brownfield import")
    expect(vcenter["vmSize"], snapshot["targetSpec"]["vcenterVmSize"], "vCenter size")
    expect(vcenter["storageSize"], snapshot["targetSpec"]["vcenterStorageSize"], "vCenter storage size")

    expect(artifact["dnsSpec"]["subdomain"], inventory["dns"]["subdomain"], "DNS subdomain")
    expect(artifact["dnsSpec"]["nameservers"], inventory["dns"]["nameservers"], "DNS servers")
    expect(artifact["ntpServers"], inventory["dns"]["ntpServers"], "NTP servers")

    expected_networks = {
        item["networkType"]: item for item in inventory["targetNetworks"]
    }
    actual_networks = index_unique(artifact["networkSpecs"], "networkType", "network type")
    expect(set(actual_networks), set(expected_networks), "target network coverage")
    for network_type, expected_network in expected_networks.items():
        actual_network = actual_networks[network_type]
        for field in ("subnet", "gateway", "subnetMask", "vlanId", "mtu"):
            expect(actual_network[field], expected_network[field], f"{network_type}.{field}")

    operations = artifact["vcfOperationsSpec"]
    operations_expected = snapshot["targetSpec"]["operations"]
    expect(operations["applianceSize"], operations_expected["applianceSize"], "VCF Operations size")
    expect(operations["version"], target, "VCF Operations target")
    expect(operations["useExistingDeployment"], operations_expected["useExistingDeployment"], "VCF Operations import")
    expect(
        operations["loadBalancerFqdn"],
        inventory["targetEndpoints"]["operationsLoadBalancerFqdn"],
        "VCF Operations load balancer",
    )
    expected_nodes = {
        (item["hostname"], item["type"]) for item in inventory["targetEndpoints"]["operationsNodes"]
    }
    actual_nodes = {(item["hostname"], item["type"]) for item in operations["nodes"]}
    expect(actual_nodes, expected_nodes, "VCF Operations nodes")
    expect(len(actual_nodes), operations_expected["nodeCount"], "VCF Operations node count")

    automation = artifact["vcfAutomationSpec"]
    automation_expected = snapshot["targetSpec"]["automation"]
    expect(automation["hostname"], inventory["targetEndpoints"]["automationFqdn"], "Automation FQDN")
    expect(automation["platformFqdn"], inventory["targetEndpoints"]["vspPlatformFqdn"], "Automation platform")
    expect(automation["internalClusterCidr"], inventory["targetEndpoints"]["automationInternalClusterCidr"], "Automation CIDR")
    expect(automation["ipPool"], inventory["targetEndpoints"]["automationIpPool"], "Automation IP pool")
    expect(automation["size"], automation_expected["size"], "Automation size")
    expect(automation["nodePrefix"], automation_expected["nodePrefix"], "Automation node prefix")
    expect(automation["version"], target, "Automation target")
    expect(automation["useExistingDeployment"], automation_expected["useExistingDeployment"], "Automation import")

    vsp = artifact["vspClusterSpec"]
    vsp_expected = snapshot["targetSpec"]["vsp"]
    endpoints = inventory["targetEndpoints"]
    expect(vsp["platformFqdn"], endpoints["vspPlatformFqdn"], "VSP platform FQDN")
    expect(vsp["instanceFqdn"], endpoints["vspInstanceFqdn"], "VSP instance FQDN")
    expect(vsp["fleetFqdn"], endpoints["vspFleetFqdn"], "VSP fleet FQDN")
    expect(vsp["size"], vsp_expected["size"], "VSP size")
    expect(vsp["internalClusterCidrIpv4"], vsp_expected["internalClusterCidrIpv4"], "VSP internal CIDR")
    expect(vsp["version"], target, "VSP target")
    expect(vsp["useExistingDeployment"], vsp_expected["useExistingDeployment"], "VSP deployment mode")
    expect(vsp["ipv4Pool"]["cidr"], endpoints["vspIpv4Cidr"], "VSP IPv4 CIDR")
    expect(vsp["ipv4Pool"]["addresses"], endpoints["vspIpv4Addresses"], "VSP IPv4 addresses")


def verify_architecture(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    architecture = artifact["architecture"]
    expected_domains = index_unique(snapshot["targetArchitecture"]["domains"], "id", "expected domain")
    actual_domains = index_unique(architecture["domains"], "id", "domain")
    expect(set(actual_domains), set(expected_domains), "domain coverage")
    for domain_id, expected_domain in expected_domains.items():
        for field, value in expected_domain.items():
            expect(actual_domains[domain_id][field], value, f"architecture domain {domain_id}.{field}")

    inventory_domains = index_unique(inventory["domains"], "id", "inventory domain")
    expect(set(actual_domains), set(inventory_domains), "inventory domain coverage")
    for domain_id, inventory_domain in inventory_domains.items():
        for field in (
            "role",
            "hostCount",
            "cpuCoresPerHost",
            "memoryGiBPerHost",
            "usableStorageTiB",
        ):
            expect(
                actual_domains[domain_id][field],
                inventory_domain[field],
                f"inventory capacity binding {domain_id}.{field}",
            )

    expected_services = index_unique(snapshot["targetArchitecture"]["services"], "id", "expected service")
    actual_services = index_unique(architecture["services"], "id", "service")
    expect(set(actual_services), set(expected_services), "management service coverage")
    endpoint_fields = {
        "vcf-operations": "operationsLoadBalancerFqdn",
        "vcf-automation": "automationFqdn",
        "vcf-operations-for-logs": "logsTargetFqdn",
    }
    for service_id, expected_service in expected_services.items():
        actual = actual_services[service_id]
        for field in (
            "displayName",
            "placementDomain",
            "deploymentModel",
            "size",
            "nodeCount",
        ):
            expect(actual[field], expected_service[field], f"service {service_id}.{field}")
        expect(
            actual["fqdn"],
            inventory["targetEndpoints"][endpoint_fields[service_id]],
            f"service {service_id}.fqdn",
        )
        expect(actual["capacity"]["metric"], expected_service["capacityMetric"], f"service {service_id} capacity metric")
        expect(actual["capacity"]["value"], expected_service["capacityValue"], f"service {service_id} capacity value")
        if "retentionDays" in expected_service:
            expect(actual["capacity"]["retentionDays"], expected_service["retentionDays"], "log retention")
            expect(actual["capacity"]["usableStorageTiB"], expected_service["usableStorageTiB"], "log storage")

    requirements = inventory["serviceRequirements"]
    expect(actual_services["vcf-operations"]["capacity"]["value"], requirements["managedObjects"], "Operations capacity binding")
    expect(actual_services["vcf-automation"]["capacity"]["value"], requirements["automationConcurrentDeployments"], "Automation capacity binding")
    expect(actual_services["vcf-operations-for-logs"]["capacity"]["value"], requirements["logIngestGiBPerDay"], "Logs ingest binding")
    expect(actual_services["vcf-operations-for-logs"]["capacity"]["retentionDays"], requirements["logRetentionDays"], "Logs retention binding")
    if actual_services["vcf-operations-for-logs"]["fqdn"] == inventory["targetEndpoints"]["logsSourceFqdn"]:
        fail("VCF Operations for Logs 9.1 must use a distinct side-by-side target FQDN")


def verify_migration_plan(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    plan = artifact["migrationPlan"]
    source = inventory["platformVersion"]
    target = inventory["targetVersion"]
    expect(plan["sourcePlatformVersion"], source, "source platform version")
    expect(plan["targetPlatformVersion"], target, "target platform version")
    expect(plan["platformUpgradePath"], [source, target], "platform upgrade path")
    supported_edges = {(item["from"], item["to"]) for item in snapshot["supportedPlatformEdges"]}
    for edge in zip(plan["platformUpgradePath"], plan["platformUpgradePath"][1:]):
        if edge not in supported_edges:
            fail(f"unsupported platform hop in migration plan: {edge[0]} -> {edge[1]}")

    inventory_components = index_unique(inventory["components"], "id", "inventory component")
    rules = index_unique(snapshot["componentRules"], "componentId", "compatibility rule")
    steps = plan["steps"]
    expect([step["sequence"] for step in steps], list(range(1, len(steps) + 1)), "step sequence")
    actual_steps = index_unique(steps, "componentId", "migration component")
    expect(set(actual_steps), set(inventory_components), "migration component coverage")
    expect(set(rules), set(inventory_components), "snapshot component coverage")
    positions = {step["componentId"]: step["sequence"] for step in steps}

    for component_id, component in inventory_components.items():
        step = actual_steps[component_id]
        rule = rules[component_id]
        expect(step["componentName"], component["name"], f"{component_id} name")
        expect(step["componentKind"], component["kind"], f"{component_id} kind")
        expect(step["currentVersion"], component["currentVersion"], f"{component_id} current version")
        expect(step["targetVersion"], rule["targetVersion"], f"{component_id} target")
        expect(step["upgradePath"], rule["supportedPath"], f"{component_id} supported path")
        expect(step["action"], rule["action"], f"{component_id} action")
        expect(step["dependsOn"], rule["dependsOn"], f"{component_id} dependencies")
        expect(step["gates"], rule["requiredGates"], f"{component_id} gates")
        for dependency in step["dependsOn"]:
            if positions[dependency] >= step["sequence"]:
                fail(f"{component_id} appears before dependency {dependency}")

        if "sdkResourceType" in component:
            if "sdkResourceSpec" not in step:
                fail(f"{component_id} is missing its VMware.Sdk.Vcf resource upgrade spec")
            sdk_spec = step["sdkResourceSpec"]
            expect(sdk_spec["resourceId"], component_id, f"{component_id} SDK resource id")
            expect(sdk_spec["type"], component["sdkResourceType"], f"{component_id} SDK resource type")
            expect(sdk_spec["toVersion"], rule["targetVersion"], f"{component_id} SDK target")
            expect(sdk_spec["upgradeNow"], False, f"{component_id} SDK planning mode")
        elif "sdkResourceSpec" in step:
            fail(f"{component_id} must not invent an SDDC Manager resource upgrade spec")


def main() -> None:
    run_module(ARTIFACT)

    # Binding requirement: validate the complete top-level artifact as the pinned
    # installer's own SddcSpec before any extension or semantic acceptance checks.
    artifact = load_json(ARTIFACT)
    installer_document = load_json(INSTALLER_SCHEMA)
    sddc_schema = installer_document["components"]["schemas"]["SddcSpec"]
    validate_json_schema(artifact, sddc_schema, installer_document)

    snapshot = load_json(ROOT / "data" / "compatibility-snapshot.json")
    actual_schema_hash = hashlib.sha256(INSTALLER_SCHEMA.read_bytes()).hexdigest()
    expect(actual_schema_hash, snapshot["installerSchema"]["sha256"], "pinned installer schema hash")

    plan_schema = load_json(PLAN_SCHEMA)
    validate_json_schema(artifact, plan_schema, plan_schema)
    inventory = load_json(ROOT / "data" / "estate-inventory.json")

    expect(artifact["artifactVersion"], "1.0", "artifact version")
    expect(artifact["sourceEstateId"], inventory["estateId"], "source estate")
    expect(artifact["targetVersion"], inventory["targetVersion"], "artifact target")
    expect(snapshot["targetVersion"], inventory["targetVersion"], "snapshot target")

    verify_installer_spec(artifact, inventory, snapshot)
    verify_architecture(artifact, inventory, snapshot)
    verify_migration_plan(artifact, inventory, snapshot)
    verify_research_notes()

    second_output = ROOT / "output" / ".verification-second.json"
    try:
        run_module(second_output)
        second_artifact = load_json(second_output)
        expect(second_artifact, artifact, "deterministic repeated generation")
    finally:
        second_output.unlink(missing_ok=True)

    print("verified: VCF 9.1 brownfield migration architecture is valid and complete")


if __name__ == "__main__":
    try:
        main()
    except (VerificationError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
