#!/usr/bin/env python3
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


class ValidationError(Exception):
    pass


class JsonSchemaValidator:
    """Small, dependency-free validator for the keywords used by the pinned schemas."""

    def __init__(self, document):
        self.document = document

    def resolve(self, ref):
        if not ref.startswith("#/"):
            raise ValidationError(f"unsupported non-local schema reference: {ref}")
        value = self.document
        for token in ref[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            value = value[token]
        return value

    def validate(self, value, schema, path="$"):
        errors = []
        if "$ref" in schema:
            return self.validate(value, self.resolve(schema["$ref"]), path)

        if "allOf" in schema:
            for sub in schema["allOf"]:
                errors.extend(self.validate(value, sub, path))
        if "anyOf" in schema:
            branches = [self.validate(value, sub, path) for sub in schema["anyOf"]]
            if all(branch for branch in branches):
                errors.append(f"{path}: does not satisfy anyOf")
        if "oneOf" in schema:
            matches = sum(not self.validate(value, sub, path) for sub in schema["oneOf"])
            if matches != 1:
                errors.append(f"{path}: must satisfy exactly one oneOf branch")

        if "const" in schema and value != schema["const"]:
            errors.append(f"{path}: must equal {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: {value!r} is not in {schema['enum']!r}")

        expected = schema.get("type")
        type_ok = True
        if expected == "object":
            type_ok = isinstance(value, dict)
        elif expected == "array":
            type_ok = isinstance(value, list)
        elif expected == "string":
            type_ok = isinstance(value, str)
        elif expected == "integer":
            type_ok = isinstance(value, int) and not isinstance(value, bool)
        elif expected == "number":
            type_ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif expected == "boolean":
            type_ok = isinstance(value, bool)
        elif expected == "null":
            type_ok = value is None
        if expected and not type_ok:
            return errors + [f"{path}: expected {expected}, got {type(value).__name__}"]

        if isinstance(value, dict):
            for key in schema.get("required", []):
                if key not in value:
                    errors.append(f"{path}: missing required property {key!r}")
            properties = schema.get("properties", {})
            for key, child in value.items():
                if key in properties:
                    errors.extend(self.validate(child, properties[key], f"{path}.{key}"))
                elif schema.get("additionalProperties") is False:
                    errors.append(f"{path}: additional property {key!r} is not allowed")
                elif isinstance(schema.get("additionalProperties"), dict):
                    errors.extend(self.validate(child, schema["additionalProperties"], f"{path}.{key}"))
            if "minProperties" in schema and len(value) < schema["minProperties"]:
                errors.append(f"{path}: has too few properties")
            if "maxProperties" in schema and len(value) > schema["maxProperties"]:
                errors.append(f"{path}: has too many properties")

        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                errors.append(f"{path}: has fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append(f"{path}: has more than {schema['maxItems']} items")
            if schema.get("uniqueItems"):
                encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
                if len(encoded) != len(set(encoded)):
                    errors.append(f"{path}: items must be unique")
            if isinstance(schema.get("items"), dict):
                for index, child in enumerate(value):
                    errors.extend(self.validate(child, schema["items"], f"{path}[{index}]"))

        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                errors.append(f"{path}: shorter than {schema['minLength']}")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                errors.append(f"{path}: longer than {schema['maxLength']}")
            if "pattern" in schema:
                try:
                    if re.search(schema["pattern"], value) is None:
                        errors.append(f"{path}: does not match {schema['pattern']!r}")
                except re.error as exc:
                    raise ValidationError(f"invalid pinned schema pattern at {path}: {exc}") from exc

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{path}: below minimum {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{path}: above maximum {schema['maximum']}")
            if isinstance(schema.get("exclusiveMinimum"), (int, float)) and not isinstance(schema["exclusiveMinimum"], bool):
                if value <= schema["exclusiveMinimum"]:
                    errors.append(f"{path}: not above exclusive minimum")
            if isinstance(schema.get("exclusiveMaximum"), (int, float)) and not isinstance(schema["exclusiveMaximum"], bool):
                if value >= schema["exclusiveMaximum"]:
                    errors.append(f"{path}: not below exclusive maximum")
        return errors


def fail(message):
    raise ValidationError(message)


def require(condition, message):
    if not condition:
        fail(message)


def load_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def run_client():
    with tempfile.TemporaryDirectory(prefix="vcfarch-classes-") as classes:
        try:
            compile_result = subprocess.run(
                ["javac", "-encoding", "UTF-8", "-d", classes, "ArchitectureClient.java", "TestMain.java"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            fail("Java compilation timed out")
        if compile_result.returncode != 0:
            fail("Java compilation failed:\n" + compile_result.stderr)
        try:
            run_result = subprocess.run(
                ["java", "-cp", classes, "TestMain"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            fail("TestMain timed out")
        if run_result.returncode != 0:
            fail("TestMain failed:\n" + run_result.stderr)
        require(not run_result.stderr, "TestMain must not emit stderr output")
        try:
            artifact = json.loads(run_result.stdout)
        except json.JSONDecodeError as exc:
            fail(f"TestMain output is not one JSON artifact: {exc}")
        require(isinstance(artifact, dict), "TestMain output must be one JSON object")
        return artifact


def check_source_layout():
    java_sources = {path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*.java")}
    require(
        java_sources == {"ArchitectureClient.java", "TestMain.java"},
        "implementation must remain in ArchitectureClient.java with no additional Java sources",
    )


def check_research_record(artifact):
    sources = artifact.get("researchConsulted")
    require(isinstance(sources, list) and sources, "researchConsulted must be a non-empty array")
    required_fields = {"title", "url", "accessed", "finding"}
    for index, source in enumerate(sources):
        require(isinstance(source, dict), f"researchConsulted[{index}] must be an object")
        require(required_fields <= set(source), f"researchConsulted[{index}] is missing required fields")
        for field in required_fields:
            require(
                isinstance(source[field], str) and source[field].strip(),
                f"researchConsulted[{index}].{field} must be a nonblank string",
            )
        parsed = urlparse(source["url"])
        require(
            parsed.scheme == "https" and bool(parsed.hostname),
            f"researchConsulted[{index}].url must be an absolute HTTPS URL",
        )
        require(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", source["accessed"]) is not None,
            f"researchConsulted[{index}].accessed must use YYYY-MM-DD",
        )
        try:
            date.fromisoformat(source["accessed"])
        except ValueError:
            fail(f"researchConsulted[{index}].accessed is not a calendar date")


def validate_installer_schema_first(artifact):
    openapi = load_json(ROOT / "specifications/vcf-installer/vcf-installer-openapi.json")
    try:
        sddc_spec = artifact["greenfield"]["sddcSpec"]
    except (KeyError, TypeError):
        sddc_spec = None
    schema = openapi["components"]["schemas"]["SddcSpec"]
    errors = JsonSchemaValidator(openapi).validate(sddc_spec, schema, "$.greenfield.sddcSpec")
    if errors:
        fail("SddcSpec schema validation failed before architecture checks:\n" + "\n".join(errors[:30]))
    return sddc_spec


def check_sddc_spec(spec, req, snapshot):
    baseline = snapshot["greenfieldBaseline"]
    require(spec.get("sddcId") == req["sddcId"], "wrong SDDC id")
    require(spec.get("workflowType") == req["workflowType"], "workflow must be greenfield VCF")
    require(spec.get("version") == baseline["vcf"] == req["vcfVersion"], "wrong VCF version")
    require(spec.get("vcfInstanceName") == req["vcfInstanceName"], "wrong VCF instance name")

    hosts = spec.get("hostSpecs", [])
    require([item.get("hostname") for item in hosts] == req["hosts"], "hostSpecs must name every required host in order")

    vcenter = spec.get("vcenterSpec", {})
    require(vcenter.get("vcenterHostname") == req["appliances"]["vcenterHostname"], "wrong vCenter hostname")
    require(vcenter.get("version") == baseline["vcenter"], "wrong vCenter baseline")
    require(vcenter.get("useExistingDeployment") is False, "vCenter must be a new deployment")

    manager = spec.get("sddcManagerSpec", {})
    require(manager.get("hostname") == req["appliances"]["sddcManagerHostname"], "wrong SDDC Manager hostname")
    require(manager.get("version") == baseline["sddcManager"], "wrong SDDC Manager baseline")
    require(manager.get("useExistingDeployment") is False, "SDDC Manager must be new")

    nsx = spec.get("nsxtSpec", {})
    require(nsx.get("vipFqdn") == req["appliances"]["nsxVipFqdn"], "wrong NSX VIP")
    require(nsx.get("version") == baseline["nsx"], "wrong NSX baseline")
    require(nsx.get("useExistingDeployment") is False, "NSX must be new")
    require(
        [item.get("hostname") for item in nsx.get("nsxtManagers", [])]
        == req["appliances"]["nsxManagerHostnames"],
        "NSX manager set is incomplete",
    )
    require(len(nsx.get("nsxtManagers", [])) == req["availability"]["nsxManagerCount"], "wrong NSX manager count")

    require(spec.get("dnsSpec") == req["dns"], "DNS design differs from requirements")
    require(spec.get("ntpServers") == req["ntpServers"], "NTP design differs from requirements")

    actual_networks = {item.get("networkType"): item for item in spec.get("networkSpecs", [])}
    require(set(actual_networks) == {item["networkType"] for item in req["networks"]}, "network set differs from requirements")
    for expected in req["networks"]:
        actual = actual_networks[expected["networkType"]]
        for key in ("networkType", "vlanId", "subnet", "gateway", "mtu"):
            require(actual.get(key) == expected[key], f"{expected['networkType']} has wrong {key}")

    actual_switches = {item.get("dvsName"): item for item in spec.get("dvsSpecs", [])}
    require(set(actual_switches) == {item["name"] for item in req["distributedSwitches"]}, "distributed switch set is wrong")
    for expected in req["distributedSwitches"]:
        actual = actual_switches[expected["name"]]
        require(actual.get("networks") == expected["networks"], f"{expected['name']} network attachment is wrong")
        require(actual.get("vmnicsToUplinks") == expected["vmnicsToUplinks"], f"{expected['name']} pNIC layout is wrong")
        require(actual.get("mtu") == expected["mtu"], f"{expected['name']} MTU is wrong")

    datastore = spec.get("datastoreSpec", {}).get("vsanSpec", {})
    datastore_req = req["installerChoices"]["datastore"]
    require(
        datastore.get("esaConfig", {}).get("enabled") is (datastore_req["type"] == "VSAN_ESA"),
        "vSAN architecture is wrong",
    )
    require(datastore.get("datastoreName") == datastore_req["name"], "vSAN datastore name is wrong")
    require(datastore.get("failuresToTolerate") == req["availability"]["hostFailuresToTolerate"], "wrong vSAN FTT")
    require(
        spec.get("securitySpec") == req["installerChoices"]["security"],
        "installer security choices are wrong",
    )


def check_capacity_and_site(greenfield, req):
    capacity = greenfield.get("capacityPlan", {})
    source = req["capacity"]
    count = source["hostCount"]
    per_host = source["perHost"]
    require(capacity.get("hostCount") == count, "capacity plan host count is wrong")
    require(capacity.get("perHost") == per_host, "capacity plan per-host values are wrong")
    raw = {
        "physicalCores": count * per_host["physicalCores"],
        "memoryGiB": count * per_host["memoryGiB"],
        "rawNvmeTiB": count * per_host["rawNvmeTiB"],
    }
    require(capacity.get("rawTotals") == raw, "raw capacity totals are wrong")
    surviving = count - req["availability"]["hostFailuresToTolerate"]
    storage = source["storagePlanning"]
    usable = surviving * per_host["rawNvmeTiB"] / storage["raid1DataCopies"]
    usable *= (100 - storage["reservedSlackPercent"]) / 100
    after_failure = {
        "physicalCores": surviving * per_host["physicalCores"],
        "memoryGiB": surviving * per_host["memoryGiB"],
        "usableNvmeTiB": int(usable) if usable.is_integer() else usable,
    }
    require(capacity.get("afterOneHostFailure") == after_failure, "post-failure capacity calculation is wrong")
    needed = source["requiredAfterOneHostFailure"]
    require(all(after_failure[key] >= needed[key] for key in needed), "fixture capacity cannot meet stated post-failure demand")
    require(capacity.get("requirementsMet") is True, "capacity plan must record that demand is met")

    site = greenfield.get("siteDesign", {})
    site_req = req["site"]
    require(site.get("siteCode") == site_req["siteCode"], "wrong site")
    require(site.get("stretched") is site_req["stretched"], "wrong stretched-site choice")
    domains = site.get("faultDomains", [])
    require([item.get("id") for item in domains] == site_req["faultDomains"], "fault domains are wrong")
    placed_hosts = []
    for domain in domains:
        require(set(domain.get("topOfRackSwitches", [])) == set(site_req["topOfRackSwitches"]), "each rack must be dual-homed")
        require(1 <= len(domain.get("hosts", [])) <= 3, "hosts are not spread across rack fault domains")
        placed_hosts.extend(domain.get("hosts", []))
    require(sorted(placed_hosts) == sorted(req["hosts"]) and len(placed_hosts) == len(set(placed_hosts)), "host placement is incomplete or duplicated")
    placements = site.get("nsxManagerPlacement", [])
    require({item.get("manager") for item in placements} == set(req["appliances"]["nsxManagerHostnames"]), "NSX Manager placement is incomplete")
    require({item.get("faultDomain") for item in placements} == set(site_req["faultDomains"]), "NSX Managers must span all rack fault domains")


def check_edge(greenfield, req, snapshot):
    edge = greenfield.get("edgeDesign", {})
    traffic = req["edgeTraffic"]
    capable = [
        profile for profile in snapshot["edgeProfiles"]
        if profile["maxNorthSouthGbpsPerNode"] >= traffic["requiredNorthSouthGbpsAfterOneNodeFailure"]
        and profile["minimumUplinkSpeedGbps"] <= traffic["minimumPhysicalUplinkSpeedGbps"]
    ]
    require(capable, "pinned compatibility snapshot has no capable Edge profile")
    selected = min(capable, key=lambda profile: profile["sizeRank"])
    require(edge.get("formFactor") == selected["formFactor"], "Edge is not the smallest form factor meeting failover throughput")
    require(edge.get("ratedNorthSouthGbpsPerNode") == selected["maxNorthSouthGbpsPerNode"], "Edge rating is wrong")
    require(edge.get("requiredNorthSouthGbpsAfterOneNodeFailure") == traffic["requiredNorthSouthGbpsAfterOneNodeFailure"], "Edge demand is not recorded")
    require(edge.get("nodeCount") == req["availability"]["edgeNodeCount"], "wrong Edge node count")
    require(edge.get("haMode") == req["availability"]["edgeHaMode"], "wrong Edge HA mode")

    layouts = {item["id"]: item for item in snapshot["supportedEdgeLayouts"]}
    layout_id = edge.get("uplinkLayoutId")
    require(layout_id in layouts, "unknown Edge uplink layout")
    layout = layouts[layout_id]
    require(selected["formFactor"] in layout["formFactors"], "uplink layout is not supported for selected Edge")
    require(layout["distinctTopOfRackSwitches"] is True, "uplink layout is not fault-diverse")
    require(
        layout["uplinkSpeedGbps"] >= traffic["requiredNorthSouthGbpsAfterOneNodeFailure"],
        "one surviving Edge uplink cannot carry the required north-south traffic",
    )
    require(edge.get("teamingPolicy") == layout["teamingPolicy"], "wrong Edge teaming policy")
    nodes = edge.get("nodes", [])
    require(len(nodes) == req["availability"]["edgeNodeCount"], "Edge node list is incomplete")
    require(len({node.get("id") for node in nodes}) == len(nodes), "Edge node ids must be unique")
    require(len({node.get("faultDomain") for node in nodes}) == len(nodes), "Edge nodes must occupy different rack fault domains")
    allowed_domains = set(req["site"]["faultDomains"])
    allowed_tors = set(req["site"]["topOfRackSwitches"])
    for node in nodes:
        require(node.get("faultDomain") in allowed_domains, "Edge placed outside a fault domain")
        uplinks = node.get("uplinks", [])
        require(len(uplinks) == layout["uplinksPerEdge"] == traffic["uplinksPerEdge"], "wrong uplink count per Edge")
        require({item.get("tor") for item in uplinks} == allowed_tors, "Edge uplinks must use distinct ToRs")
        require(len({item.get("name") for item in uplinks}) == len(uplinks), "Edge interface names must be unique")
        for uplink in uplinks:
            require(uplink.get("speedGbps") == layout["uplinkSpeedGbps"], "wrong Edge uplink speed")
            require(uplink.get("role") == "TIER0", "Edge data uplink role must be TIER0")


def check_migration(artifact, inventory, snapshot, plan_schema):
    plan = artifact.get("existingEstateMigration")
    schema_errors = JsonSchemaValidator(plan_schema).validate(plan, plan_schema)
    if schema_errors:
        fail("migration plan schema validation failed:\n" + "\n".join(schema_errors[:30]))
    require(plan["inventoryId"] == inventory["inventoryId"], "migration plan references the wrong inventory")
    components = {item["id"]: item for item in inventory["components"]}
    rules = {item["componentId"]: item for item in snapshot["migrationRules"]}
    steps = plan["steps"]
    require(len(steps) == len(components), "migration plan must contain one step per inventory component")
    require({item["componentId"] for item in steps} == set(components), "migration plan component coverage is wrong")
    require([item["sequence"] for item in steps] == list(range(1, len(steps) + 1)), "migration sequence must be contiguous and ordered")
    sequence_by_id = {item["componentId"]: item["sequence"] for item in steps}
    for step in steps:
        component = components[step["componentId"]]
        require(step["componentName"] == component["name"], f"wrong component name for {component['id']}")
        require(step["currentVersion"] == component["currentVersion"], f"wrong current version for {component['id']}")
        require(component["id"] in rules, f"no pinned transition for {component['id']}")
        rule = rules[component["id"]]
        require(rule["fromVersion"] == component["currentVersion"], f"pinned transition source mismatch for {component['id']}")
        require(step["targetComponentName"] == rule["targetComponentName"], f"wrong target component for {component['id']}")
        require(step["targetVersion"] == rule["targetVersion"], f"wrong target for {component['id']}")
        require(step["action"] == rule["action"], f"wrong action for {component['id']}")
        require(set(step["gates"]) == set(rule["requiredGates"]), f"missing or extra gates for {component['id']}")
        for dependency in rule["dependsOn"]:
            require(sequence_by_id[dependency] < step["sequence"], f"{component['id']} must follow {dependency}")


def main():
    check_source_layout()
    artifact = run_client()

    # This is intentionally the first semantic validation performed on the artifact.
    sddc_spec = validate_installer_schema_first(artifact)

    req = load_json(ROOT / "fixtures/greenfield-requirements.json")
    inventory = load_json(ROOT / "fixtures/existing-estate.json")
    snapshot = load_json(ROOT / "fixtures/compatibility-snapshot.json")
    plan_schema = load_json(ROOT / "fixtures/migration-plan-schema.json")

    require(isinstance(artifact.get("greenfield"), dict), "greenfield architecture is missing")
    require(
        artifact["greenfield"].get("componentVersions") == snapshot["greenfieldBaseline"],
        "greenfield componentVersions must record the pinned VCF bill of materials",
    )
    check_sddc_spec(sddc_spec, req, snapshot)
    check_capacity_and_site(artifact["greenfield"], req)
    check_edge(artifact["greenfield"], req, snapshot)
    check_migration(artifact, inventory, snapshot, plan_schema)
    check_research_record(artifact)
    print("PASS: VCF architecture, migration, and research artifact satisfies the pinned contracts")


if __name__ == "__main__":
    try:
        main()
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
