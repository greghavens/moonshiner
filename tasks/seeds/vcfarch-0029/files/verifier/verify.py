#!/usr/bin/env python3
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


class ValidationError(Exception):
    pass


def load_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def resolve_ref(root_schema, ref):
    if not ref.startswith("#/"):
        raise ValidationError(f"external schema reference is not supported: {ref}")
    value = root_schema
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[token]
    return value


def type_matches(value, expected):
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
    raise ValidationError(f"unsupported JSON Schema type {expected!r}")


def validate_json_schema(value, schema, root_schema, path="$", seen=None):
    if isinstance(schema, bool):
        if not schema:
            raise ValidationError(f"{path}: rejected by false schema")
        return
    if not isinstance(schema, dict):
        raise ValidationError(f"{path}: malformed schema")
    if seen is None:
        seen = set()

    if "$ref" in schema:
        marker = (id(value), schema["$ref"])
        if marker in seen:
            return
        seen.add(marker)
        validate_json_schema(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path, seen)
        seen.remove(marker)

    for branch in schema.get("allOf", []):
        validate_json_schema(value, branch, root_schema, path, seen)

    for keyword, wanted in (("anyOf", "at least one"), ("oneOf", "exactly one")):
        if keyword in schema:
            matches = 0
            for branch in schema[keyword]:
                try:
                    validate_json_schema(value, branch, root_schema, path, set(seen))
                    matches += 1
                except ValidationError:
                    pass
            valid = matches >= 1 if keyword == "anyOf" else matches == 1
            if not valid:
                raise ValidationError(f"{path}: {keyword} matched {matches}, expected {wanted}")

    if "not" in schema:
        try:
            validate_json_schema(value, schema["not"], root_schema, path, set(seen))
        except ValidationError:
            pass
        else:
            raise ValidationError(f"{path}: matched forbidden schema")

    if value is None and schema.get("nullable"):
        return

    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(value, item) for item in types):
            raise ValidationError(f"{path}: expected type {expected_type}, got {type(value).__name__}")

    if "const" in schema and value != schema["const"]:
        raise ValidationError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValidationError(f"{path}: {value!r} is not in enum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                raise ValidationError(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        for name, child in value.items():
            child_path = f"{path}.{name}"
            if name in properties:
                validate_json_schema(child, properties[name], root_schema, child_path, seen)
                continue
            matched = False
            for pattern, child_schema in pattern_properties.items():
                if re.search(pattern, name):
                    validate_json_schema(child, child_schema, root_schema, child_path, seen)
                    matched = True
            if matched:
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                raise ValidationError(f"{path}: additional property {name!r} is forbidden")
            if isinstance(additional, dict):
                validate_json_schema(child, additional, root_schema, child_path, seen)
        if len(value) < schema.get("minProperties", 0):
            raise ValidationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise ValidationError(f"{path}: too many properties")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValidationError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValidationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ValidationError(f"{path}: items are not unique")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, child in enumerate(value):
                validate_json_schema(child, items, root_schema, f"{path}[{index}]", seen)

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValidationError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValidationError(f"{path}: string is too long")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                raise ValidationError(f"{path}: invalid schema regex: {exc}") from exc
            if not matched:
                raise ValidationError(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValidationError(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValidationError(f"{path}: above maximum")
        if isinstance(schema.get("exclusiveMinimum"), (int, float)) and not isinstance(schema.get("exclusiveMinimum"), bool):
            if value <= schema["exclusiveMinimum"]:
                raise ValidationError(f"{path}: not above exclusive minimum")
        if isinstance(schema.get("exclusiveMaximum"), (int, float)) and not isinstance(schema.get("exclusiveMaximum"), bool):
            if value >= schema["exclusiveMaximum"]:
                raise ValidationError(f"{path}: not below exclusive maximum")
        if "multipleOf" in schema and not math.isclose(value % schema["multipleOf"], 0):
            raise ValidationError(f"{path}: not a multiple of {schema['multipleOf']}")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expected_networks(requirements):
    return [
        {
            "networkType": item["type"],
            "vlanId": item["vlan"],
            "subnet": item["cidr"],
            "gateway": item["gateway"],
            "subnetMask": item["subnetMask"],
            "mtu": item["mtu"],
            "ipAddressVersion": "IPv4",
            "ipAddressAssignmentMode": "STATIC",
        }
        for item in requirements["networks"]
    ]


def check_secret_placeholders(value, path="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if "password" in key.lower():
                require(isinstance(child, str) and re.fullmatch(r"\$\{[A-Z0-9_]+\}", child),
                        f"{child_path} must be an uppercase secret placeholder")
            else:
                check_secret_placeholders(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_secret_placeholders(child, f"{path}[{index}]")


def check_research(path):
    text = path.read_text(encoding="utf-8")
    require(re.search(r"\b20\d{2}-\d{2}-\d{2}\b", text) is not None,
            "research.md must record an ISO access date")
    entries = []
    for line in text.splitlines():
        match = re.search(r"https://[^\s)>]+", line)
        if match is None:
            continue
        title = line[:match.start()].strip(" -*#—–:([")
        point = line[match.end():].strip(" -*#—–:).]")
        if title and point:
            entries.append((title, match.group(), point))
    require(len(entries) >= 2,
            "research.md must contain at least two titled source records with HTTPS URLs and checked points")
    broadcom_entries = []
    for title, url, point in entries:
        host = (urlparse(url).hostname or "").lower()
        if host == "broadcom.com" or host.endswith(".broadcom.com"):
            broadcom_entries.append((title, url, point))
    require(len(broadcom_entries) >= 2,
            "research.md must cite at least two HTTPS Broadcom-published sources")
    checked_text = " ".join(f"{title} {point}" for title, _, point in broadcom_entries).lower()
    require("9.1" in checked_text, "research.md must identify the researched VCF 9.1 target")
    require(any(term in checked_text for term in ("compatibility", "interoperability", "combination")),
            "research.md must record a compatibility or interoperability point")
    require(any(term in checked_text for term in ("upgrade", "sequence", "sequencing", "migration")),
            "research.md must record an upgrade or sequencing point")


def expected_architecture(requirements, snapshot):
    primary, recovery = requirements["sites"]
    fqdn = requirements["managementFqdns"]
    versions = snapshot["supportedCombination"]
    sizing = snapshot["sizing"]
    capacity = requirements["capacity"]
    return {
        "schemaVersion": "1.0",
        "designId": requirements["designId"],
        "target": versions,
        "sites": [
            {
                "id": site["id"],
                "role": site["role"],
                "managementHosts": len(site["managementHosts"]),
                "workloadHosts": len(site["workloadHosts"]),
                "hostCores": site["hostCores"],
                "hostMemoryGiB": site["hostMemoryGiB"],
                "usableStorageTiB": site["usableStorageTiB"],
            }
            for site in requirements["sites"]
        ],
        "domains": [
            {"name": "dal01-m01", "kind": "MANAGEMENT", "site": primary["id"],
             "hostCount": len(primary["managementHosts"]),
             "hostFailuresToTolerate": requirements["availability"]["managementHostFailures"]},
            {"name": "dal01-w01", "kind": "WORKLOAD", "site": primary["id"],
             "hostCount": len(primary["workloadHosts"]),
             "hostFailuresToTolerate": requirements["availability"]["primaryWorkloadHostFailures"]},
            {"name": "stl01-w01", "kind": "WORKLOAD", "site": recovery["id"],
             "hostCount": len(recovery["workloadHosts"]),
             "hostFailuresToTolerate": requirements["availability"]["recoveryWorkloadHostFailures"]},
        ],
        "products": [
            {
                "name": "VCF Operations",
                "version": versions["vcfOperations"],
                "size": sizing["vcfOperations"]["size"],
                "nodes": sizing["vcfOperations"]["nodes"],
                "site": primary["id"],
                "domain": "dal01-m01",
                "deploymentModel": "appliance-cluster",
                "remoteCollectors": [fqdn["operationsCollector"]],
                "managedObjects": capacity["monitoredObjects"],
            },
            {
                "name": "VCF Automation",
                "version": versions["vcfAutomation"],
                "size": sizing["vcfAutomation"]["size"],
                "nodes": sizing["vcfAutomation"]["nodes"],
                "site": primary["id"],
                "domain": "dal01-m01",
                "deploymentModel": "vcf-automation-cluster",
                "managedMachines": capacity["automationManagedMachines"],
                "concurrentDeployments": capacity["automationConcurrentDeployments"],
            },
            {
                "name": "VCF Operations for Logs / VCF Log Management",
                "version": versions["vcfLogManagement"],
                "size": sizing["vcfLogManagement"]["size"],
                "nodes": sizing["vcfLogManagement"]["replicas"],
                "site": primary["id"],
                "domain": "dal01-m01",
                "deploymentModel": sizing["vcfLogManagement"]["deploymentModel"],
                "ingestGiBPerDay": capacity["logIngestGiBPerDay"],
                "hotRetentionDays": capacity["logHotRetentionDays"],
            },
        ],
        "capacity": capacity,
        "availability": requirements["availability"],
    }


def check_semantics(sddc, architecture, plan, requirements, estate, snapshot):
    primary = requirements["sites"][0]
    fqdn = requirements["managementFqdns"]
    sizing = snapshot["sizing"]
    versions = snapshot["supportedCombination"]

    require(sddc.get("sddcId") == "dal01-m01", "unexpected sddcId")
    require(sddc.get("workflowType") == "VCF", "workflowType must be VCF")
    require(sddc.get("version") == requirements["targetVersion"] == versions["vcf"], "VCF version mismatch")
    require(sddc.get("vcfInstanceName") == requirements["vcfInstanceName"], "VCF instance name mismatch")
    require(sddc.get("dnsSpec") == requirements["dns"], "DNS design mismatch")
    require(sddc.get("ntpServers") == requirements["ntpServers"], "NTP design mismatch")
    require(sddc.get("networkSpecs") == expected_networks(requirements), "network design mismatch")
    require([host.get("hostname") for host in sddc.get("hostSpecs", [])] == primary["managementHosts"],
            "management host inventory mismatch")
    require(sddc.get("clusterSpec") == {"datacenterName": "DAL01-DC01", "clusterName": "dal01-m01-cl01"},
            "management cluster placement mismatch")
    require(sddc.get("datastoreSpec", {}).get("vsanSpec") == {
        "datastoreName": "dal01-m01-vsan01", "failuresToTolerate": 2, "vsanDedup": False
    }, "management vSAN design mismatch")
    require(sddc.get("vcenterSpec", {}).get("vcenterHostname") == fqdn["vcenter"], "vCenter placement mismatch")
    require(sddc.get("sddcManagerSpec", {}).get("hostname") == fqdn["sddcManager"], "SDDC Manager placement mismatch")
    nsx = sddc.get("nsxtSpec", {})
    require([node.get("hostname") for node in nsx.get("nsxtManagers", [])] == fqdn["nsxManagers"],
            "NSX manager placement mismatch")
    require(nsx.get("vipFqdn") == fqdn["nsxVip"] and nsx.get("version") == versions["nsx"], "NSX target mismatch")

    operations = sddc.get("vcfOperationsSpec", {})
    require([node.get("hostname") for node in operations.get("nodes", [])] == fqdn["operationsNodes"],
            "VCF Operations nodes mismatch")
    require(operations.get("applianceSize") == sizing["vcfOperations"]["size"], "VCF Operations size mismatch")
    require(operations.get("version") == versions["vcfOperations"], "VCF Operations version mismatch")
    require(operations.get("loadBalancerFqdn") == fqdn["operationsVip"], "VCF Operations VIP mismatch")
    collector = sddc.get("vcfOperationsCollectorSpec", {})
    require(collector.get("hostname") == fqdn["operationsCollector"] and collector.get("version") == versions["vcfOperations"],
            "recovery-site Operations collector mismatch")

    automation = sddc.get("vcfAutomationSpec", {})
    require(automation.get("hostname") == fqdn["automation"], "VCF Automation hostname mismatch")
    require(automation.get("platformFqdn") == fqdn["automationPlatform"], "VCF Automation platform mismatch")
    require(automation.get("size") == sizing["vcfAutomation"]["size"], "VCF Automation size mismatch")
    require(automation.get("version") == versions["vcfAutomation"], "VCF Automation version mismatch")
    require(len(automation.get("ipPool", [])) == sizing["automationIpCount"], "VCF Automation IP pool mismatch")
    platform = sddc.get("vspClusterSpec", {})
    require(platform.get("platformFqdn") == fqdn["managementPlatform"], "management platform FQDN mismatch")
    require(platform.get("instanceFqdn") == fqdn["vcfInstance"], "VCF instance FQDN mismatch")
    require(platform.get("version") == versions["vcf"], "management platform version mismatch")
    require(len(platform.get("ipv4Pool", {}).get("addresses", [])) == sizing["managementPlatformIpCount"],
            "management platform must reserve the pinned IP count")
    check_secret_placeholders(sddc)

    require(architecture == expected_architecture(requirements, snapshot), "greenfield architecture does not match requirements")

    expected_steps = [
        {
            "order": rule["order"],
            "component": rule["component"],
            "currentVersion": rule["currentVersion"],
            "target": rule["target"],
            "action": rule["action"],
            "gates": rule["gates"],
        }
        for rule in snapshot["upgradePlan"]
    ]
    expected_plan = {
        "schemaVersion": "1.0",
        "estateId": estate["estateId"],
        "targetVcfVersion": versions["vcf"],
        "sourceSnapshot": snapshot["snapshotId"],
        "steps": expected_steps,
    }
    require(plan == expected_plan, "migration plan differs from the pinned ordered authority")
    inventory = {(item["name"], item["version"]) for item in estate["components"]}
    planned = {(item["component"], item["currentVersion"]) for item in plan["steps"]}
    require(planned == inventory and len(plan["steps"]) == len(estate["components"]),
            "migration plan does not cover every inventory component exactly once")


def main():
    subprocess.run(["go", "run", "./cmd/vcfdesign"], cwd=ROOT, check=True)

    # The first validation is deliberately the upstream installer schema. Do not
    # move fixture, snapshot, migration, or semantic checks above this block.
    openapi = load_json(ROOT / "specifications/vcf-installer/vcf-installer-openapi.json")
    sddc = load_json(ROOT / "out/greenfield-sddc.json")
    validate_json_schema(sddc, openapi["components"]["schemas"]["SddcSpec"], openapi)

    migration_schema = load_json(ROOT / "schemas/migration-plan.schema.json")
    plan = load_json(ROOT / "out/migration-plan.json")
    validate_json_schema(plan, migration_schema, migration_schema)

    requirements = load_json(ROOT / "fixtures/requirements.json")
    estate = load_json(ROOT / "fixtures/estate.json")
    snapshot = load_json(ROOT / "fixtures/compatibility-snapshot.json")
    architecture = load_json(ROOT / "out/greenfield-architecture.json")
    check_research(ROOT / "research.md")
    check_semantics(sddc, architecture, plan, requirements, estate, snapshot)

    subprocess.run(["go", "test", "-race", "./..."], cwd=ROOT, check=True)
    print("verification passed: installer schema, pinned architecture, migration plan, go test -race")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, ValidationError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
