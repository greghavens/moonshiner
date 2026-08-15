#!/usr/bin/env python3
"""Protected verifier for vcfarch-0031."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any


FILES = Path(__file__).resolve().parents[1]
INVENTORY_PATH = FILES / "fixtures" / "estate-inventory.json"
SNAPSHOT_PATH = FILES / "fixtures" / "compatibility-snapshot.json"
PLAN_SCHEMA_PATH = FILES / "fixtures" / "migration-plan-schema.json"
OPENAPI_PATH = FILES / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
CLIENT_PATH = FILES / "DesignClient.java"

EXPECTED_SHA256 = {
    "installer specification": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "estate inventory": "9d075e6407d47e5437bbec65a1255eb66286aff69fcab1c1f50a5a5d7a345163",
    "compatibility snapshot": "460eec20cec9d2a2dbd96eb0517760d9f330d4586c44dabd0ec7ee82c381fbd6",
    "migration plan schema": "e71cb58ffbad73d479f11b68964b3504b0dcb195d8d1f96474e1a01b81631bf0",
}

PROTECTED_TEST_MAIN = r"""
import java.nio.file.Path;

public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: TestMain <inventory> <snapshot>");
        }
        String artifact = DesignClient.buildArchitecture(Path.of(args[0]), Path.of(args[1]));
        if (artifact == null) {
            throw new IllegalStateException("DesignClient returned null");
        }
        System.out.print(artifact);
    }
}
"""


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot load {path.name}: {exc}")


def resolve_ref(root: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        fail(f"unsupported non-local schema reference: {ref}")
    value: Any = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            fail(f"unresolved schema reference: {ref}")
        value = value[part]
    return value


def type_matches(instance: Any, expected: str) -> bool:
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
    fail(f"unsupported JSON Schema type {expected!r}")


def schema_errors(instance: Any, schema: Any, root: dict[str, Any], path: str = "$") -> list[str]:
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: rejected by false schema"]
    if not isinstance(schema, dict):
        return [f"{path}: malformed schema"]
    if "$ref" in schema:
        return schema_errors(instance, resolve_ref(root, schema["$ref"]), root, path)

    errors: list[str] = []
    if "allOf" in schema:
        for part in schema["allOf"]:
            errors.extend(schema_errors(instance, part, root, path))
    if "anyOf" in schema:
        if not any(not schema_errors(instance, part, root, path) for part in schema["anyOf"]):
            errors.append(f"{path}: does not match anyOf")
    if "oneOf" in schema:
        matches = sum(not schema_errors(instance, part, root, path) for part in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: matches {matches} oneOf branches")
    if "not" in schema and not schema_errors(instance, schema["not"], root, path):
        errors.append(f"{path}: matches forbidden schema")

    if instance is None and schema.get("nullable") is True:
        return errors
    expected_type = schema.get("type")
    if expected_type is not None:
        candidates = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(instance, candidate) for candidate in candidates):
            return errors + [f"{path}: expected {expected_type}, got {type(instance).__name__}"]

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errors.append(f"{path}: fewer than minProperties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            errors.append(f"{path}: more than maxProperties")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(schema_errors(value, properties[key], root, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(schema_errors(value, schema["additionalProperties"], root, child_path))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{path}: items are not unique")
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(schema_errors(item, schema["items"], root, f"{path}[{index}]"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], instance) is None:
                    errors.append(f"{path}: does not match pattern")
            except re.error as exc:
                errors.append(f"{path}: invalid schema pattern: {exc}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum")
        if isinstance(schema.get("exclusiveMinimum"), (int, float)) and not isinstance(
            schema.get("exclusiveMinimum"), bool
        ) and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: not above exclusiveMinimum")
        if isinstance(schema.get("exclusiveMaximum"), (int, float)) and not isinstance(
            schema.get("exclusiveMaximum"), bool
        ) and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: not below exclusiveMaximum")
        if "multipleOf" in schema:
            quotient = instance / schema["multipleOf"]
            if not math.isclose(quotient, round(quotient), rel_tol=0, abs_tol=1e-9):
                errors.append(f"{path}: not a multipleOf {schema['multipleOf']}")
    return errors


def validate(instance: Any, schema: dict[str, Any], root: dict[str, Any], label: str) -> None:
    errors = schema_errors(instance, schema, root)
    if errors:
        excerpt = "\n  - ".join(errors[:20])
        fail(f"{label} validation failed:\n  - {excerpt}")


def run_client() -> dict[str, Any]:
    if not CLIENT_PATH.is_file():
        fail("DesignClient.java is missing")
    with tempfile.TemporaryDirectory(prefix="vcfarch-0031-") as temp_name:
        temp = Path(temp_name)
        harness = temp / "TestMain.java"
        harness.write_text(PROTECTED_TEST_MAIN, encoding="utf-8")
        compile_result = subprocess.run(
            ["javac", "-encoding", "UTF-8", "-d", str(temp), str(CLIENT_PATH), str(harness)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if compile_result.returncode != 0:
            fail(f"Java compilation failed:\n{compile_result.stderr.strip()}")
        run_result = subprocess.run(
            [
                "java",
                "-cp",
                str(temp),
                "TestMain",
                str(INVENTORY_PATH),
                str(SNAPSHOT_PATH),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if run_result.returncode != 0:
            fail(f"TestMain failed:\n{run_result.stderr.strip()}")
        try:
            artifact = json.loads(run_result.stdout)
        except json.JSONDecodeError as exc:
            fail(f"client output is not exactly one JSON value: {exc}")
        if not isinstance(artifact, dict):
            fail("client output must be a JSON object")
        return artifact


def verify_pinned_files() -> None:
    paths = {
        "installer specification": OPENAPI_PATH,
        "estate inventory": INVENTORY_PATH,
        "compatibility snapshot": SNAPSHOT_PATH,
        "migration plan schema": PLAN_SCHEMA_PATH,
    }
    for label, path in paths.items():
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            fail(f"cannot read pinned {label}: {exc}")
        if digest != EXPECTED_SHA256[label]:
            fail(f"pinned {label} was modified")


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"{label} must be an array")
    return value


def verify_greenfield(artifact: dict[str, Any], snapshot: dict[str, Any]) -> None:
    green = snapshot["greenfield"]
    require_equal(artifact.get("sddcId"), green["sddcId"], "sddcId")
    require_equal(artifact.get("workflowType"), green["workflowType"], "workflowType")
    require_equal(artifact.get("version"), snapshot["targetRelease"], "SddcSpec version")
    require_equal(artifact.get("dnsSpec"), {
        "subdomain": green["dnsDomain"],
        "nameservers": green["dnsServers"],
    }, "dnsSpec")
    require_equal(artifact.get("ntpServers"), green["ntpServers"], "ntpServers")

    expected_hosts = green["hostnamesBySite"]["SITE-A"] + green["hostnamesBySite"]["SITE-B"]
    host_specs = require_list(artifact.get("hostSpecs"), "hostSpecs")
    actual_hosts = [item.get("hostname") for item in host_specs if isinstance(item, dict)]
    require_equal(actual_hosts, expected_hosts, "ordered management host list")

    datastore = require_mapping(artifact.get("datastoreSpec"), "datastoreSpec")
    vsan = require_mapping(datastore.get("vsanSpec"), "datastoreSpec.vsanSpec")
    require_equal(vsan.get("failuresToTolerate"), 1, "vSAN FTT")
    require_equal(require_mapping(vsan.get("esaConfig"), "vSAN ESA config").get("enabled"), True, "vSAN ESA")

    expected_networks = [
        {
            "networkType": item["type"],
            "vlanId": item["vlanId"],
            "subnet": item["subnet"],
            "gateway": item["gateway"],
            "mtu": item["mtu"],
        }
        for item in green["networks"]
    ]
    require_equal(artifact.get("networkSpecs"), expected_networks, "networkSpecs")

    architecture = require_mapping(artifact.get("x-architecture"), "x-architecture")
    require_equal(architecture.get("designType"), "GREENFIELD", "design type")
    require_equal(architecture.get("billOfMaterials"), green["billOfMaterials"], "bill of materials")
    require_equal(architecture.get("networks"), green["networks"], "architecture networks")
    require_equal(architecture.get("stretchedVlans"), green["stretchedVlans"], "stretched VLANs")

    sites = require_list(architecture.get("sites"), "architecture sites")
    by_id = {site.get("id"): site for site in sites if isinstance(site, dict)}
    require_equal(set(by_id), {"SITE-A", "SITE-B", "SITE-W"}, "site IDs")
    for site_id in green["dataSites"]:
        require_equal(by_id[site_id].get("role"), "DATA", f"{site_id} role")
        require_equal(by_id[site_id].get("hostnames"), green["hostnamesBySite"][site_id], f"{site_id} hosts")
    require_equal(by_id[green["witnessSite"]].get("role"), "WITNESS", "witness site role")
    require_equal(by_id[green["witnessSite"]].get("hostnames"), [], "witness site data hosts")

    witness = require_mapping(architecture.get("witness"), "witness")
    require_equal(witness.get("siteId"), green["witnessSite"], "witness site")
    require_equal(witness.get("fqdn"), green["witnessFqdn"], "witness FQDN")
    require_equal(witness.get("storesWorkloadData"), False, "witness workload-data flag")
    require_equal(witness.get("independentPowerCoolingNetwork"), True, "witness independence")
    if witness.get("fqdn") in actual_hosts:
        fail("the witness must not be a management data host")

    availability = require_mapping(architecture.get("availability"), "availability")
    require_equal(availability.get("topology"), "STRETCHED", "availability topology")
    require_equal(availability.get("vsanArchitecture"), "ESA", "vSAN architecture")
    require_equal(availability.get("replicationMode"), "SYNCHRONOUS", "site replication mode")
    require_equal(availability.get("failuresToTolerate"), 1, "availability FTT")
    require_equal(availability.get("rpoMinutes"), 0, "RPO")
    inter_rtt = availability.get("interSiteRttMs")
    witness_rtt = availability.get("witnessRttMsFromEachDataSite")
    if not isinstance(inter_rtt, (int, float)) or isinstance(inter_rtt, bool) or not 0 < inter_rtt <= 5:
        fail("interSiteRttMs must be greater than zero and no more than 5")
    if not isinstance(witness_rtt, (int, float)) or isinstance(witness_rtt, bool) or not 0 < witness_rtt <= 200:
        fail("witnessRttMsFromEachDataSite must be greater than zero and no more than 200")

    capacity = require_mapping(architecture.get("capacity"), "capacity")
    require_equal(capacity.get("perHost"), green["perHostCapacity"], "per-host capacity")
    require_equal(capacity.get("protectedDemand"), green["protectedDemand"], "protected demand")
    require_equal(capacity.get("minimumAfterHeadroom"), green["minimumAfterHeadroom"], "headroom demand")
    require_equal(capacity.get("survivingSiteCapacity"), green["survivingSiteCapacity"], "surviving-site capacity")
    minimum = green["minimumAfterHeadroom"]
    surviving = green["survivingSiteCapacity"]
    demand = green["protectedDemand"]
    headroom_fraction = demand["headroomPercent"] / 100
    for key in ("physicalCores", "memoryGiB", "usableStorageTiB"):
        calculated_minimum = demand[key] / (1 - headroom_fraction)
        if not math.isclose(minimum[key], calculated_minimum, rel_tol=0, abs_tol=1e-9):
            fail(f"pinned minimumAfterHeadroom {key} is mathematically inconsistent")
        if surviving[key] < minimum[key]:
            fail(f"pinned surviving-site {key} does not satisfy demand")


def verify_research(artifact: dict[str, Any]) -> None:
    research = require_mapping(artifact.get("x-research"), "x-research")
    sources = require_list(research.get("sources"), "x-research.sources")
    if len(sources) < 2:
        fail("x-research.sources must contain at least two Broadcom-published sources")

    urls: set[str] = set()
    informed_facts: list[str] = []
    broadcom_source_count = 0
    for index, value in enumerate(sources):
        source = require_mapping(value, f"x-research.sources[{index}]")
        for key in ("title", "url", "informed"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                fail(f"x-research.sources[{index}].{key} must be a non-empty string")

        parsed = urllib.parse.urlparse(source["url"])
        hostname = (parsed.hostname or "").lower()
        reserved_suffixes = (".example", ".invalid", ".localhost", ".test")
        if (
            parsed.scheme != "https"
            or "." not in hostname
            or hostname == "localhost"
            or hostname.endswith(reserved_suffixes)
        ):
            fail(f"x-research.sources[{index}].url must be a real HTTPS source URL")
        if hostname == "broadcom.com" or hostname.endswith(".broadcom.com"):
            broadcom_source_count += 1
        if source["url"] in urls:
            fail("x-research source URLs must be unique")
        urls.add(source["url"])
        informed_facts.append(source["informed"].lower())

    coverage = " ".join(informed_facts)
    if broadcom_source_count < 2:
        fail("x-research must include at least two Broadcom-published sources")
    if "compatib" not in coverage or "upgrade" not in coverage:
        fail("x-research must record both compatibility and upgrade-path research")


def verify_migration(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    plan = artifact.get("x-migrationPlan")
    plan_schema = load_json(PLAN_SCHEMA_PATH)
    validate(plan, plan_schema, plan_schema, "migration plan schema")
    require_equal(plan.get("targetRelease"), snapshot["targetRelease"], "migration target release")

    inventory_by_id = {item["inventoryId"]: item for item in inventory["components"]}
    expected_rows = snapshot["migration"]
    steps = plan["steps"]
    require_equal(len(steps), len(inventory_by_id), "migration step count")
    require_equal([step["order"] for step in steps], list(range(1, len(steps) + 1)), "migration order")
    require_equal({step["inventoryId"] for step in steps}, set(inventory_by_id), "migration inventory coverage")

    for step, expected in zip(steps, expected_rows):
        item = inventory_by_id.get(step["inventoryId"])
        if item is None:
            fail(f"unknown migration inventoryId {step['inventoryId']!r}")
        require_equal(step["order"], expected["order"], f"{step['inventoryId']} order")
        require_equal(step["component"], item["component"], f"{step['inventoryId']} component")
        require_equal(step["currentVersion"], item["version"], f"{step['inventoryId']} current version")
        for key in ("targetComponent", "targetVersion", "action", "gates"):
            require_equal(step[key], expected[key], f"{step['inventoryId']} {key}")


def main() -> int:
    artifact = run_client()

    # Required ordering: validate the produced artifact against the installer
    # specification's own SddcSpec schema before any fixture/architecture checks.
    openapi = load_json(OPENAPI_PATH)
    try:
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError):
        fail("installer specification does not contain components.schemas.SddcSpec")
    validate(artifact, sddc_schema, openapi, "VCF Installer SddcSpec")

    verify_pinned_files()
    inventory = load_json(INVENTORY_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    verify_greenfield(artifact, snapshot)
    verify_migration(artifact, inventory, snapshot)
    verify_research(artifact)
    print("PASS: schema-valid VCF 9.1 architecture and pinned migration plan")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
