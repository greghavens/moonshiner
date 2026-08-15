#!/usr/bin/env python3
"""Offline acceptance verifier for the VCF architecture artifact."""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"


class VerificationFailure(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationFailure(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read JSON {path.relative_to(ROOT)}: {error}")


def run_checked(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if process.returncode != 0:
        detail = (process.stdout + "\n" + process.stderr).strip()[-3000:]
        fail(f"command failed ({' '.join(command)}):\n{detail}")
    return process


def resolve_ref(document: dict[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        fail(f"installer schema uses unsupported external reference {reference!r}")
    node: Any = document
    for encoded in reference[2:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            fail(f"installer schema reference does not resolve: {reference}")
        node = node[token]
    return node


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
    return True


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    """Validate the OpenAPI/JSON-Schema keywords used by the pinned SddcSpec graph."""
    if "$ref" in schema:
        validate_schema(value, resolve_ref(document, schema["$ref"]), document, path, errors)
        return

    if value is None and schema.get("nullable") is True:
        return

    for branch in schema.get("allOf", []):
        validate_schema(value, branch, document, path, errors)

    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            branch_errors: list[str] = []
            validate_schema(value, branch, document, path, branch_errors)
            matches += not branch_errors
        if matches != 1:
            errors.append(f"{path}: expected exactly one oneOf branch, got {matches}")
        return

    expected_type = schema.get("type")
    if expected_type and not json_type_matches(value, expected_type):
        errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
        return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum {schema['enum']!r}")

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                validate_schema(child, properties[name], document, f"{path}.{name}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(
                    child,
                    schema["additionalProperties"],
                    document,
                    f"{path}.{name}",
                    errors,
                )

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: has more than {schema['maxItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in value}) != len(value):
            errors.append(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                validate_schema(child, item_schema, document, f"{path}[{index}]", errors)

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value) is not None
            except re.error as error:
                fail(f"invalid regular expression in installer schema at {path}: {error}")
            if not matched:
                errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")


def validate_sddc_spec_first(artifact: Any, openapi: dict[str, Any]) -> None:
    """The installer schema is intentionally the first artifact assertion."""
    try:
        schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError):
        fail("pinned installer specification has no SddcSpec schema")

    if not isinstance(artifact, dict):
        fail("installer SddcSpec validation failed: architecture root is not an object")
    candidate = artifact.get("greenfieldSddcSpec")
    errors: list[str] = []
    validate_schema(candidate, schema, openapi, "$.greenfieldSddcSpec", errors)
    if errors:
        fail("installer SddcSpec validation failed:\n" + "\n".join(errors[:30]))


def expect_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"{label} must be an array")
    return value


def expect_object_contains(actual: Any, expected: dict[str, Any], label: str) -> None:
    candidate = require_object(actual, label)
    for name, value in expected.items():
        expect_equal(candidate.get(name), value, f"{label}.{name}")


def expect_unordered_list(actual: Any, expected: list[Any], label: str) -> None:
    candidate = require_list(actual, label)
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    expect_equal(
        sorted(canonical(value) for value in candidate),
        sorted(canonical(value) for value in expected),
        label,
    )


def expected_networks(request: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for network in request["networks"]:
        result.append(
            {
                "networkType": network["networkType"],
                "subnet": network["subnet"],
                "gateway": network["gateway"],
                "includeIpAddressRanges": [
                    {
                        "startIpAddress": network["startIpAddress"],
                        "endIpAddress": network["endIpAddress"],
                    }
                ],
                "vlanId": network["vlanId"],
                "mtu": network["mtu"],
            }
        )
    return result


def check_greenfield_spec(artifact: dict[str, Any], inventory: dict[str, Any]) -> None:
    request = inventory["requestedWorkloadDomain"]
    spec = require_object(artifact["greenfieldSddcSpec"], "greenfieldSddcSpec")
    expect_equal(spec.get("sddcId"), request["domainId"], "SddcSpec.sddcId")
    expect_equal(spec.get("workflowType"), "VCF_EXTEND", "SddcSpec.workflowType")
    expect_equal(spec.get("version"), request["targetVersion"], "SddcSpec.version")
    if "sddcManagerSpec" in spec:
        fail("greenfieldSddcSpec must not alter or redeploy SDDC Manager")

    all_hosts = [
        hostname
        for site in request["sites"]["data"]
        for hostname in site["hostnames"]
    ]
    host_specs = require_list(spec.get("hostSpecs"), "SddcSpec.hostSpecs")
    actual_hosts = [
        require_object(host, f"SddcSpec.hostSpecs[{index}]").get("hostname")
        for index, host in enumerate(host_specs)
    ]
    expect_equal(sorted(actual_hosts), sorted(all_hosts), "SddcSpec host assignments")

    vcenter = request["vcenter"]
    expect_object_contains(
        spec.get("vcenterSpec"),
        {
            "vcenterHostname": vcenter["hostname"],
            "rootVcenterPassword": vcenter["rootPassword"],
            "vmSize": vcenter["vmSize"],
            "storageSize": vcenter["storageSize"],
            "useExistingDeployment": False,
            "version": request["targetVersion"],
        },
        "SddcSpec.vcenterSpec",
    )

    nsx = request["nsx"]
    nsxt_spec = require_object(spec.get("nsxtSpec"), "SddcSpec.nsxtSpec")
    manager_specs = require_list(
        nsxt_spec.get("nsxtManagers"), "SddcSpec.nsxtSpec.nsxtManagers"
    )
    actual_managers = [
        require_object(manager, f"SddcSpec.nsxtSpec.nsxtManagers[{index}]").get(
            "hostname"
        )
        for index, manager in enumerate(manager_specs)
    ]
    expect_equal(
        sorted(actual_managers),
        sorted(nsx["managerHostnames"]),
        "SddcSpec NSX managers",
    )
    expect_object_contains(
        nsxt_spec,
        {
            "nsxtManagerSize": nsx["managerSize"],
            "vipFqdn": nsx["vipFqdn"],
            "transportVlanId": nsx["transportVlanId"],
            "useExistingDeployment": False,
            "version": request["targetVersion"],
        },
        "SddcSpec.nsxtSpec",
    )
    expect_object_contains(spec.get("dnsSpec"), request["dns"], "SddcSpec.dnsSpec")

    network_specs = require_list(spec.get("networkSpecs"), "SddcSpec.networkSpecs")
    actual_networks: dict[str, dict[str, Any]] = {}
    for index, network_value in enumerate(network_specs):
        network = require_object(network_value, f"SddcSpec.networkSpecs[{index}]")
        network_type = network.get("networkType")
        if not isinstance(network_type, str) or network_type in actual_networks:
            fail("SddcSpec.networkSpecs must have unique string networkType values")
        actual_networks[network_type] = network
    expected_by_type = {
        network["networkType"]: network for network in expected_networks(request)
    }
    expect_equal(set(actual_networks), set(expected_by_type), "SddcSpec network types")
    for network_type, expected in expected_by_type.items():
        expect_object_contains(
            actual_networks[network_type],
            expected,
            f"SddcSpec.networkSpecs[{network_type}]",
        )

    switch = request["distributedSwitch"]
    dvs_specs = require_list(spec.get("dvsSpecs"), "SddcSpec.dvsSpecs")
    expect_equal(len(dvs_specs), 1, "SddcSpec.dvsSpecs count")
    dvs = require_object(dvs_specs[0], "SddcSpec.dvsSpecs[0]")
    expect_equal(dvs.get("dvsName"), switch["name"], "SddcSpec DVS name")
    expect_equal(dvs.get("mtu"), switch["mtu"], "SddcSpec DVS MTU")
    expect_unordered_list(
        dvs.get("networks"),
        [network["networkType"] for network in request["networks"]],
        "SddcSpec DVS networks",
    )
    expect_unordered_list(
        dvs.get("vmnicsToUplinks"),
        switch["vmnicsToUplinks"],
        "SddcSpec DVS uplink mappings",
    )
    datastore = request["datastore"]
    datastore_spec = require_object(
        spec.get("datastoreSpec"), "SddcSpec.datastoreSpec"
    )
    vsan_spec = require_object(
        datastore_spec.get("vsanSpec"), "SddcSpec.datastoreSpec.vsanSpec"
    )
    expect_object_contains(
        vsan_spec,
        {
            "datastoreName": datastore["name"],
            "failuresToTolerate": datastore["failuresToTolerate"],
        },
        "SddcSpec.datastoreSpec.vsanSpec",
    )
    expect_object_contains(
        vsan_spec.get("esaConfig"),
        {"enabled": datastore["esaEnabled"]},
        "SddcSpec.datastoreSpec.vsanSpec.esaConfig",
    )


def check_capacity_and_availability(
    artifact: dict[str, Any], inventory: dict[str, Any]
) -> None:
    request = inventory["requestedWorkloadDomain"]
    sites = request["sites"]["data"]
    if len(sites) != 2 or len(sites[0]["hostnames"]) != len(sites[1]["hostnames"]):
        fail("protected estate fixture no longer describes two equal data sites")
    per_site = len(sites[0]["hostnames"])
    total_hosts = sum(len(site["hostnames"]) for site in sites)
    per_host = request["perHostCapacity"]
    reserve = request["reservePercent"]
    surviving = {
        "vcpu": per_site * per_host["physicalCores"] * request["cpuOvercommitRatio"],
        "memoryGiB": math.floor(per_site * per_host["memoryGiB"] * (100 - reserve) / 100),
        "usableStorageGiB": math.floor(
            per_site * per_host["rawStorageGiB"] * (100 - reserve) / 100
        ),
    }
    meets = all(
        surviving[name] >= request["capacityRequired"][name]
        for name in ("vcpu", "memoryGiB", "usableStorageGiB")
    )
    capacity_plan = require_object(artifact.get("capacityPlan"), "capacityPlan")
    expected_capacity = {
        "required": request["capacityRequired"],
        "perHost": per_host,
        "plannedHostCount": total_hosts,
        "dataHostsPerSite": per_site,
        "cpuOvercommitRatio": request["cpuOvercommitRatio"],
        "reservePercent": reserve,
        "survivingSiteCapacity": surviving,
        "meetsSingleSiteLoss": meets,
    }
    for name, expected in expected_capacity.items():
        if isinstance(expected, dict):
            expect_object_contains(
                capacity_plan.get(name), expected, f"capacityPlan.{name}"
            )
        else:
            expect_equal(capacity_plan.get(name), expected, f"capacityPlan.{name}")
    if not meets:
        fail("fixture design cannot meet its single-site-loss capacity requirement")

    expected_availability = {
        "topology": "VSAN_STRETCHED_CLUSTER",
        "failureRequirement": "SINGLE_SITE_LOSS",
        "dataSites": [
            {"id": site["id"], "hosts": site["hostnames"]}
            for site in sites
        ],
        "witnessSite": request["sites"]["witness"]["id"],
        "managementDomainChange": "NONE",
    }
    availability_plan = require_object(artifact.get("availabilityPlan"), "availabilityPlan")
    for name in ("topology", "failureRequirement", "witnessSite", "managementDomainChange"):
        expect_equal(
            availability_plan.get(name),
            expected_availability[name],
            f"availabilityPlan.{name}",
        )
    actual_sites = require_list(
        availability_plan.get("dataSites"), "availabilityPlan.dataSites"
    )
    sites_by_id: dict[str, dict[str, Any]] = {}
    for index, site_value in enumerate(actual_sites):
        site = require_object(site_value, f"availabilityPlan.dataSites[{index}]")
        site_id = site.get("id")
        if not isinstance(site_id, str) or site_id in sites_by_id:
            fail("availabilityPlan.dataSites must have unique string id values")
        sites_by_id[site_id] = site
    expected_sites = {site["id"]: site for site in expected_availability["dataSites"]}
    expect_equal(set(sites_by_id), set(expected_sites), "availabilityPlan data-site IDs")
    for site_id, expected in expected_sites.items():
        expect_unordered_list(
            sites_by_id[site_id].get("hosts"),
            expected["hosts"],
            f"availabilityPlan.dataSites[{site_id}].hosts",
        )


def check_migration_plan(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    components = inventory["components"]
    by_id = {component["componentId"]: component for component in components}
    if len(by_id) != len(components):
        fail("protected estate fixture has duplicate component IDs")

    plan = require_object(artifact.get("migrationPlan"), "migrationPlan")
    expect_equal(
        set(plan),
        {"schemaVersion", "estateId", "targetRelease", "steps"},
        "migrationPlan fields",
    )
    expect_equal(plan.get("schemaVersion"), 1, "migrationPlan.schemaVersion")
    expect_equal(plan.get("estateId"), inventory["estateId"], "migrationPlan.estateId")
    expect_equal(
        plan.get("targetRelease"), snapshot["targetRelease"], "migrationPlan.targetRelease"
    )
    steps = require_list(plan.get("steps"), "migrationPlan.steps")
    ordered = snapshot["orderedPlan"]
    expect_equal(len(steps), len(components), "migrationPlan step count")
    expect_equal(len(ordered), len(components), "compatibility snapshot step count")

    seen: set[str] = set()
    expected_fields = {
        "sequence",
        "componentId",
        "componentType",
        "currentVersion",
        "targetVersion",
        "action",
        "gates",
    }
    for index, (step_value, pinned) in enumerate(zip(steps, ordered), start=1):
        step = require_object(step_value, f"migrationPlan.steps[{index - 1}]")
        expect_equal(set(step), expected_fields, f"migrationPlan.steps[{index - 1}] fields")
        component_id = pinned["componentId"]
        if component_id not in by_id:
            fail(f"compatibility snapshot refers to unknown component {component_id!r}")
        component = by_id[component_id]
        expect_equal(
            component["currentVersion"],
            pinned["sourceVersion"],
            f"pinned source version for {component_id}",
        )
        expected = {
            "sequence": pinned["sequence"],
            "componentId": component_id,
            "componentType": component["componentType"],
            "currentVersion": component["currentVersion"],
            "targetVersion": pinned["targetVersion"],
            "action": pinned["action"],
            "gates": pinned["gates"],
        }
        expect_equal(step, expected, f"migrationPlan.steps[{index - 1}]")
        if component_id in seen:
            fail(f"migration plan repeats component {component_id!r}")
        seen.add(component_id)
    expect_equal(seen, set(by_id), "migrationPlan component coverage")

    management_id = inventory["managementDomain"]["domainId"]
    for step in steps:
        component = by_id[step["componentId"]]
        if component["domainId"] == management_id:
            if step["action"] != "RETAIN" or step["currentVersion"] != step["targetVersion"]:
                fail(f"management component {step['componentId']!r} is not retained unchanged")


def check_research_record(artifact: dict[str, Any]) -> None:
    sources = require_list(artifact.get("researchConsulted"), "researchConsulted")
    if not sources:
        fail("researchConsulted must record at least one fetched source page")
    required_fields = {"title", "url", "accessDate", "finding"}
    for index, source_value in enumerate(sources):
        source = require_object(source_value, f"researchConsulted[{index}]")
        missing = required_fields - set(source)
        if missing:
            fail(f"researchConsulted[{index}] is missing fields {sorted(missing)!r}")
        for field in required_fields:
            value = source[field]
            if not isinstance(value, str) or not value.strip():
                fail(f"researchConsulted[{index}].{field} must be a nonblank string")
        parsed = urlparse(source["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            fail(f"researchConsulted[{index}].url must be an absolute HTTP(S) URL")
        try:
            date.fromisoformat(source["accessDate"])
        except ValueError:
            fail(f"researchConsulted[{index}].accessDate must use ISO YYYY-MM-DD format")


def check_source_layout() -> None:
    java_sources = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.java")
        if ".sandbox-home" not in path.parts
    }
    expect_equal(
        java_sources,
        {"TestMain.java", "VcfArchitecture.java"},
        "Java source files",
    )
    build_files = {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    }
    unexpected = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".sandbox-home" not in path.parts
        and path.name in build_files
    )
    if unexpected:
        fail(f"unexpected build files: {unexpected!r}")
    implementation_suffixes = {
        ".class",
        ".groovy",
        ".jar",
        ".js",
        ".kt",
        ".py",
        ".scala",
        ".sh",
    }
    allowed_implementation_files = {
        "TestMain.java",
        "VcfArchitecture.java",
        "protected/verify.py",
    }
    unexpected_implementation = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".sandbox-home" not in path.parts
        and path.suffix in implementation_suffixes
        and path.relative_to(ROOT).as_posix() not in allowed_implementation_files
    )
    if unexpected_implementation:
        fail(f"unexpected implementation files: {unexpected_implementation!r}")


def protected_inputs() -> dict[Path, bytes]:
    paths = [
        ROOT / "TestMain.java",
        ROOT / ".gitignore",
        ROOT / "protected" / "verify.py",
        ROOT / "fixtures" / "estate-inventory.json",
        ROOT / "fixtures" / "compatibility-snapshot.json",
        OPENAPI_PATH,
        ROOT / "specifications" / "vcf-installer" / "official-source.json",
    ]
    try:
        return {path: path.read_bytes() for path in paths}
    except OSError as error:
        fail(f"cannot snapshot protected inputs: {error}")


def check_protected_inputs_unchanged(before: dict[Path, bytes]) -> None:
    for path, expected in before.items():
        try:
            actual = path.read_bytes()
        except OSError as error:
            fail(f"protected input {path.relative_to(ROOT)} became unreadable: {error}")
        if actual != expected:
            fail(f"implementation modified protected input {path.relative_to(ROOT)}")


def main() -> int:
    try:
        protected_before = protected_inputs()
        openapi = json.loads(protected_before[OPENAPI_PATH])
        inventory = json.loads(
            protected_before[ROOT / "fixtures" / "estate-inventory.json"]
        )
        snapshot = json.loads(
            protected_before[ROOT / "fixtures" / "compatibility-snapshot.json"]
        )
        with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
            temp = Path(temporary)
            classes = temp / "classes"
            classes.mkdir()
            artifact_path = temp / "architecture.json"
            run_checked(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "--release",
                    "17",
                    "-d",
                    str(classes),
                    "VcfArchitecture.java",
                    "TestMain.java",
                ],
                timeout=40,
            )
            run_checked(
                ["java", "-cp", str(classes), "TestMain", str(artifact_path)],
                timeout=30,
            )
            artifact = load_json(artifact_path)

            # Contract precedence: no architecture/source assertion may move above this call.
            validate_sddc_spec_first(artifact, openapi)

            check_protected_inputs_unchanged(protected_before)
            check_source_layout()
            check_greenfield_spec(artifact, inventory)
            check_capacity_and_availability(artifact, inventory)
            check_migration_plan(artifact, inventory, snapshot)
            check_research_record(artifact)
    except VerificationFailure as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as error:
        print(f"FAIL: command timed out: {error.cmd}", file=sys.stderr)
        return 1
    except Exception as error:  # fail closed with a concise diagnostic
        print(f"FAIL: unexpected verifier error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    print(
        "PASS: source layout, protected inputs, SddcSpec schema, greenfield "
        "architecture, capacity, availability, migration, and research record"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
