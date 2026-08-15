#!/usr/bin/env python3
"""Deterministic verifier for the VCF fleet architecture seed."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SDDC_PATH = ROOT / "artifacts" / "sddc-spec.json"
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
RESEARCH_PATH = ROOT / "research" / "consulted-sources.json"


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        display_path = path.relative_to(ROOT)
    except ValueError:
        display_path = path
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {display_path}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {display_path}: {exc}")


def json_type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    fail(f"unsupported JSON Schema type in protected verifier: {expected}")


def resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        fail(f"only local JSON references are supported: {pointer}")
    value = document
    for token in pointer[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        try:
            value = value[token]
        except (KeyError, TypeError):
            fail(f"unresolvable JSON reference: {pointer}")
    return value


def validate_schema(instance: Any, schema: Any, document: Any, path: str = "$") -> None:
    if isinstance(schema, bool):
        if not schema:
            fail(f"{path}: rejected by false schema")
        return
    if not isinstance(schema, dict):
        fail(f"{path}: malformed schema")

    if "$ref" in schema:
        validate_schema(instance, resolve_pointer(document, schema["$ref"]), document, path)
        return

    for subschema in schema.get("allOf", []):
        validate_schema(instance, subschema, document, path)

    for keyword, expected_matches in (("anyOf", None), ("oneOf", 1)):
        if keyword in schema:
            matches = 0
            for subschema in schema[keyword]:
                try:
                    validate_schema(instance, subschema, document, path)
                    matches += 1
                except VerificationError:
                    pass
            if matches == 0 or (expected_matches is not None and matches != expected_matches):
                fail(f"{path}: does not satisfy {keyword}")

    if "not" in schema:
        try:
            validate_schema(instance, schema["not"], document, path)
        except VerificationError:
            pass
        else:
            fail(f"{path}: matches a forbidden schema")

    if "const" in schema and instance != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{path}: value is not in the allowed enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        alternatives = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(instance, item) for item in alternatives):
            fail(f"{path}: expected JSON type {expected_type!r}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            fail(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                validate_schema(value, properties[key], document, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(value, schema["additionalProperties"], document, f"{path}.{key}")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            fail(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            fail(f"{path}: too many properties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            fail(f"{path}: too few array items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"{path}: too many array items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, value in enumerate(instance):
                validate_schema(value, item_schema, document, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            fail(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                fail(f"{path}: unsupported schema pattern: {exc}")
            if matched is None:
                fail(f"{path}: string does not match the required pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if isinstance(instance, float) and not math.isfinite(instance):
            fail(f"{path}: non-finite number")
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            fail(f"{path}: number is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            fail(f"{path}: number is not below exclusiveMaximum")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


PROTECTED_HASHES = {
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "fixtures/estate-inventory.json": "0b25e152b020af2292a8ae1111ea8a257c7b0b13a8cb022fbf36cf4b620b348f",
    "fixtures/compatibility-snapshot.json": "615e28557980f54841acd8e163a24131c2b3f74ade4371ec9c36a3b9e8659dbb",
    "schemas/migration-plan.schema.json": "f8d950b438642cabe2a4e764c10e368597a8811566911f1192ab751b72425eb3",
    "LICENSES/vcf-api-specs-Apache-2.0.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}

def verify_protected_inputs() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            fail(f"protected seed input changed: {relative}")


def expected_manifest(inventory: dict[str, Any], snapshot: dict[str, Any], storage: str) -> dict[str, Any]:
    profile = snapshot["storageProfiles"][storage]
    return {
        "schemaVersion": "1.0",
        "inventoryId": inventory["inventoryId"],
        "targetVcfVersion": snapshot["targetFleet"]["version"],
        "storageArchitecture": storage,
        "hostCount": profile["hostCount"],
        "physicalNicsPerHost": profile["physicalNicsPerHost"],
        "physicalNicSpeedGbps": profile["physicalNicSpeedGbps"],
        "networkTopology": profile["networkTopology"],
        "sddcSpecPath": "sddc-spec.json",
        "migrationPlanPath": "migration-plan.json",
    }


def verify_sddc_semantics(
    sddc: dict[str, Any],
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    storage: str,
) -> None:
    greenfield = inventory["greenfield"]
    design = greenfield["design"]
    profile = snapshot["storageProfiles"][storage]
    target = snapshot["targetFleet"]["version"]

    if manifest != expected_manifest(inventory, snapshot, storage):
        fail(f"architecture manifest does not match the pinned {storage} profile")

    expected_scalars = {
        "sddcId": greenfield["sddcId"],
        "workflowType": design["workflowType"],
        "version": target,
        "vcfInstanceName": greenfield["vcfInstanceName"],
        "managementPoolName": design["managementPoolName"],
        "ceipEnabled": design["ceipEnabled"],
        "skipEsxThumbprintValidation": design["skipEsxThumbprintValidation"],
        "skipGatewayPingValidation": design["skipGatewayPingValidation"],
    }
    for key, expected in expected_scalars.items():
        if sddc.get(key) != expected:
            fail(f"SddcSpec.{key} does not match the fixture")

    expected_hosts = greenfield["hostPool"][: profile["hostCount"]]
    actual_hosts = [host.get("hostname") for host in sddc.get("hostSpecs", [])]
    if actual_hosts != expected_hosts or len(set(actual_hosts)) != len(actual_hosts):
        fail(f"SddcSpec host selection does not match the pinned {storage} profile")

    vcenter = sddc.get("vcenterSpec", {})
    expected_vcenter = {
        "vcenterHostname": greenfield["vcenterHostname"],
        "rootVcenterPassword": greenfield["vcenterRootPassword"],
        "vmSize": design["vcenterVmSize"],
        "storageSize": design["vcenterStorageSize"],
        "ssoDomain": design["ssoDomain"],
        "version": target,
        "useExistingDeployment": design["useExistingDeployment"],
    }
    for key, expected in expected_vcenter.items():
        if vcenter.get(key) != expected:
            fail(f"SddcSpec.vcenterSpec.{key} does not match the fixture")

    if sddc.get("clusterSpec") != {
        "datacenterName": design["datacenterName"],
        "clusterName": design["clusterName"],
    }:
        fail("SddcSpec cluster naming is incorrect")
    if sddc.get("dnsSpec") != {
        "subdomain": greenfield["dnsSubdomain"],
        "nameservers": greenfield["dnsServers"],
    }:
        fail("SddcSpec DNS design is incorrect")
    if sddc.get("ntpServers") != greenfield["ntpServers"]:
        fail("SddcSpec NTP design is incorrect")

    expected_networks = greenfield["networks"]
    actual_networks = sddc.get("networkSpecs", [])
    if len(actual_networks) != len(expected_networks):
        fail("SddcSpec does not contain every fixture network")
    for actual, expected in zip(actual_networks, expected_networks):
        for key, value in expected.items():
            if actual.get(key) != value:
                fail(f"SddcSpec network {expected['networkType']} has an incorrect {key}")
        if (
            actual.get("ipAddressVersion") != design["ipAddressVersion"]
            or actual.get("ipAddressAssignmentMode") != design["ipAddressAssignmentMode"]
        ):
            fail(f"SddcSpec network {expected['networkType']} has an incorrect address mode")

    dvs_specs = sddc.get("dvsSpecs", [])
    if len(dvs_specs) != len(profile["dvs"]):
        fail(f"SddcSpec DVS count does not match the pinned {storage} network design")
    for actual, expected in zip(dvs_specs, profile["dvs"]):
        if actual.get("dvsName") != expected["name"] or actual.get("networks") != expected["networks"]:
            fail(f"SddcSpec DVS layout does not match the pinned {storage} network design")
        if actual.get("mtu") != design["dvsMtu"]:
            fail("every DVS must use the fixture's MTU")
        mappings = actual.get("vmnicsToUplinks", [])
        ids = [mapping.get("id") for mapping in mappings]
        uplinks = [mapping.get("uplink") for mapping in mappings]
        if ids != expected["vmnics"] or uplinks != [f"uplink{i + 1}" for i in range(len(ids))]:
            fail(f"SddcSpec uplinks do not match DVS {expected['name']}")

    vsan = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    if vsan.get("datastoreName") != design["datastoreName"]:
        fail("SddcSpec vSAN datastore name is incorrect")
    if vsan.get("esaConfig", {}).get("enabled") is not profile["vsanEsaEnabled"]:
        fail(f"SddcSpec ESA flag does not match the {storage} selection")
    if vsan.get("failuresToTolerate") != design["failuresToTolerate"]:
        fail("SddcSpec vSAN failuresToTolerate is incorrect")
    if vsan.get("vsanDedup") is not design["vsanDedup"]:
        fail("SddcSpec vSAN deduplication choice is incorrect")

    manager = sddc.get("sddcManagerSpec", {})
    if (
        manager.get("hostname") != greenfield["sddcManagerHostname"]
        or manager.get("version") != target
        or manager.get("useExistingDeployment") is not design["useExistingDeployment"]
    ):
        fail("SddcSpec SDDC Manager design is incorrect")
    nsx = sddc.get("nsxtSpec", {})
    if [item.get("hostname") for item in nsx.get("nsxtManagers", [])] != greenfield["nsxManagerHostnames"]:
        fail("SddcSpec NSX manager design is incorrect")
    if (
        nsx.get("vipFqdn") != greenfield["nsxVipFqdn"]
        or nsx.get("nsxtManagerSize") != design["nsxtManagerSize"]
        or nsx.get("transportVlanId") != greenfield["nsxTransportVlanId"]
        or nsx.get("version") != target
        or nsx.get("useExistingDeployment") is not design["useExistingDeployment"]
    ):
        fail("SddcSpec NSX target is incorrect")


def verify_plan(
    plan: dict[str, Any],
    plan_schema: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    validate_schema(plan, plan_schema, plan_schema)
    target = snapshot["targetFleet"]
    if plan.get("schemaVersion") != "1.0" or plan.get("inventoryId") != inventory["inventoryId"]:
        fail("migration plan identity is incorrect")
    if plan.get("targetFleet") != {
        "name": inventory["fleetName"],
        "product": target["product"],
        "version": target["version"],
        "onboardingMode": target["onboardingMode"],
    }:
        fail("migration plan fleet target is incorrect")
    if plan.get("gates") != snapshot["gateCatalog"]:
        fail("migration plan gate catalog does not match the pinned snapshot")

    components = {item["id"]: item for item in inventory["components"]}
    rules = snapshot["componentRules"]
    steps = plan.get("steps", [])
    step_ids = [step.get("componentId") for step in steps]
    if len(step_ids) != len(set(step_ids)) or set(step_ids) != set(components):
        fail("migration plan must name every inventory component exactly once")
    if step_ids != [item["componentId"] for item in sorted(steps, key=lambda item: item["order"])]:
        fail("migration plan steps are not in ascending order")

    known_gates = {gate["id"] for gate in snapshot["gateCatalog"]}
    completed: set[str] = set()
    for step in steps:
        component_id = step["componentId"]
        component = components[component_id]
        rule = rules[component_id]
        exact_fields = {
            "order": rule["order"],
            "componentName": component["name"],
            "componentType": component["type"],
            "currentVersion": component["version"],
            "targetProduct": rule["targetProduct"],
            "targetVersion": rule["targetVersion"],
            "upgradePath": rule["upgradePath"],
            "action": rule["action"],
            "gates": rule["requiredGates"],
            "after": rule["after"],
        }
        for key, expected in exact_fields.items():
            if step.get(key) != expected:
                fail(f"migration step {component_id} has an incorrect {key}")
        if not set(step["gates"]).issubset(known_gates):
            fail(f"migration step {component_id} references an unknown gate")
        if not set(step["after"]).issubset(completed):
            fail(f"migration step {component_id} has an unsatisfied ordering dependency")
        if step["upgradePath"][0] != component["version"] or step["upgradePath"][-1] != rule["targetVersion"]:
            fail(f"migration step {component_id} has incomplete path endpoints")
        completed.add(component_id)


def verify_research_record() -> None:
    record = load_json(RESEARCH_PATH)
    if not isinstance(record, dict):
        fail("research/consulted-sources.json must be a JSON object")
    sources = record.get("sources")
    if not isinstance(sources, list) or len(sources) < 2:
        fail("research record must contain multiple consulted sources")

    seen_urls: set[str] = set()
    findings: list[str] = []
    required_fields = ("title", "publisher", "url", "accessedAt", "finding")
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            fail(f"research source {index} must be an object")
        for field in required_fields:
            value = source.get(field)
            if not isinstance(value, str) or not value.strip():
                fail(f"research source {index} has an empty {field}")

        parsed = urlsplit(source["url"])
        hostname = (parsed.hostname or "").lower().rstrip(".")
        official_host = any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in ("broadcom.com", "vmware.com")
        )
        if parsed.scheme not in ("http", "https") or not official_host or not parsed.path:
            fail(f"research source {index} is not an absolute Broadcom-published HTTP(S) URL")
        normalized_url = parsed._replace(fragment="").geturl()
        if normalized_url in seen_urls:
            fail("research source URLs must be distinct")
        seen_urls.add(normalized_url)

        timestamp_text = source["accessedAt"]
        try:
            timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        except ValueError:
            fail(f"research source {index} has an invalid ISO 8601 accessedAt")
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            fail(f"research source {index} accessedAt must be a UTC timestamp")
        findings.append(source["finding"].lower())

    combined = "\n".join(findings)
    topic_checks = {
        "compatibility or interoperability": ("compatib", "interoperab"),
        "upgrade path or sequencing": ("upgrade", "sequenc", "converg"),
        "Live Site Recovery transition": ("live site recovery", "recovery", "replication"),
        "OSA/ESA storage design": ("osa", "esa", "vsan", "storage"),
    }
    for topic, terms in topic_checks.items():
        if not any(term in combined for term in terms):
            fail(f"research findings do not document the required {topic} check")


def verify_module_source() -> Path:
    manifest_path = ROOT / "VcfFleetArchitecture" / "VcfFleetArchitecture.psd1"
    module_path = ROOT / "VcfFleetArchitecture" / "VcfFleetArchitecture.psm1"
    if not manifest_path.is_file() or not module_path.is_file():
        fail("PowerShell module files are missing")
    manifest_text = manifest_path.read_text(encoding="utf-8")
    module_text = module_path.read_text(encoding="utf-8")
    if "VMware.Sdk.Vcf.Installer" not in manifest_text:
        fail("module manifest must require VMware.Sdk.Vcf.Installer")
    if "New-VcfFleetArchitecture" not in manifest_text or "New-VcfFleetArchitecture" not in module_text:
        fail("New-VcfFleetArchitecture is not exported")
    module_directory = ROOT / "VcfFleetArchitecture"
    forbidden = list(module_directory.rglob("VMware*.dll")) + list(module_directory.rglob("VMware*.nupkg"))
    if forbidden:
        fail("VMware SDK binaries must not be vendored in the repository")
    return manifest_path


def run_generator(
    module_manifest: Path,
    openapi: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    plan_schema: dict[str, Any],
    storage: str,
    expected: tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None = None,
) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        fail("pwsh is required to verify the PowerShell module")
    with tempfile.TemporaryDirectory(prefix=f"vcfarch-{storage.lower()}-") as temporary:
        output = Path(temporary)
        quote = lambda value: str(value).replace("'", "''")
        command = (
            f"$ErrorActionPreference = 'Stop'; "
            f"Import-Module '{quote(module_manifest)}' -Force; "
            f"New-VcfFleetArchitecture "
            f"-InventoryPath '{quote(ROOT / 'fixtures' / 'estate-inventory.json')}' "
            f"-CompatibilitySnapshotPath '{quote(ROOT / 'fixtures' / 'compatibility-snapshot.json')}' "
            f"-StorageArchitecture '{storage}' "
            f"-OutputDirectory '{quote(output)}'"
        )
        result = subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            fail(f"PowerShell generator failed for {storage}:\n{result.stdout.strip()}")
        generated_sddc = load_json(output / "sddc-spec.json")
        generated_manifest = load_json(output / "architecture-manifest.json")
        generated_plan = load_json(output / "migration-plan.json")
        validate_schema(generated_sddc, openapi["components"]["schemas"]["SddcSpec"], openapi)
        verify_sddc_semantics(generated_sddc, generated_manifest, inventory, snapshot, storage)
        verify_plan(generated_plan, plan_schema, inventory, snapshot)
        if expected is not None and (generated_sddc, generated_manifest, generated_plan) != expected:
            fail("selected generator output does not reproduce the committed artifacts")


def main() -> None:
    # The installer specification's own SddcSpec schema is deliberately the first
    # validation. No other repository input is read before this succeeds.
    committed_sddc = load_json(SDDC_PATH)
    openapi = load_json(OPENAPI_PATH)
    try:
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError):
        fail("pinned installer OpenAPI document has no SddcSpec schema")
    validate_schema(committed_sddc, sddc_schema, openapi)
    print("PASS: committed SddcSpec validates against the pinned VCF Installer schema")

    verify_protected_inputs()
    inventory = load_json(ROOT / "fixtures" / "estate-inventory.json")
    snapshot = load_json(ROOT / "fixtures" / "compatibility-snapshot.json")
    plan_schema = load_json(ROOT / "schemas" / "migration-plan.schema.json")
    committed_manifest = load_json(ROOT / "artifacts" / "architecture-manifest.json")
    committed_plan = load_json(ROOT / "artifacts" / "migration-plan.json")
    selected = inventory["selectedStorageArchitecture"]

    if openapi.get("info", {}).get("version") != "9.1.0.0":
        fail("installer OpenAPI version is not 9.1.0.0")
    verify_sddc_semantics(committed_sddc, committed_manifest, inventory, snapshot, selected)
    verify_plan(committed_plan, plan_schema, inventory, snapshot)
    verify_research_record()
    module_manifest = verify_module_source()

    for storage in ("OSA", "ESA"):
        expected = None
        if storage == selected:
            expected = (committed_sddc, committed_manifest, committed_plan)
        run_generator(module_manifest, openapi, inventory, snapshot, plan_schema, storage, expected)

    print("PASS: research, committed artifacts, and reproducible OSA/ESA module outputs are valid")


if __name__ == "__main__":
    try:
        main()
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
