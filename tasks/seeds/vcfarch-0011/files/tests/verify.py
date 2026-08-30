#!/usr/bin/env python3
"""Protected, deterministic offline verifier for the VCF architecture artifacts."""

from __future__ import annotations

import ast
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


class VerificationError(Exception):
    pass


class SchemaError(VerificationError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


class Validator:
    """Small standards-based validator for the schema keywords used by this seed."""

    def __init__(self, root_schema: dict[str, Any]):
        self.root = root_schema

    def validate(self, instance: Any, schema: Any, path: str = "$") -> None:
        if schema is True:
            return
        if schema is False:
            raise SchemaError(f"{path}: rejected by false schema")
        if not isinstance(schema, dict):
            raise SchemaError(f"{path}: malformed schema")

        if "$ref" in schema:
            self.validate(instance, self._resolve(schema["$ref"]), path)
            return

        if "allOf" in schema:
            for candidate in schema["allOf"]:
                self.validate(instance, candidate, path)
        if "anyOf" in schema:
            if not self._matches_any(instance, schema["anyOf"], path):
                raise SchemaError(f"{path}: does not satisfy anyOf")
        if "oneOf" in schema:
            matches = sum(self._matches(instance, candidate, path) for candidate in schema["oneOf"])
            if matches != 1:
                raise SchemaError(f"{path}: must satisfy exactly one oneOf branch (matched {matches})")
        if "not" in schema and self._matches(instance, schema["not"], path):
            raise SchemaError(f"{path}: satisfies forbidden schema")

        if "const" in schema and instance != schema["const"]:
            raise SchemaError(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and instance not in schema["enum"]:
            raise SchemaError(f"{path}: {instance!r} is not in enum")

        expected_type = schema.get("type")
        if expected_type is not None:
            allowed = expected_type if isinstance(expected_type, list) else [expected_type]
            if not any(self._is_type(instance, item) for item in allowed):
                if not (schema.get("nullable") is True and instance is None):
                    raise SchemaError(f"{path}: expected type {expected_type!r}")

        if isinstance(instance, dict):
            self._validate_object(instance, schema, path)
        elif isinstance(instance, list):
            self._validate_array(instance, schema, path)
        elif isinstance(instance, str):
            self._validate_string(instance, schema, path)
        elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
            self._validate_number(instance, schema, path)

    def _matches(self, instance: Any, schema: Any, path: str) -> bool:
        try:
            self.validate(instance, schema, path)
            return True
        except SchemaError:
            return False

    def _matches_any(self, instance: Any, schemas: list[Any], path: str) -> bool:
        return any(self._matches(instance, schema, path) for schema in schemas)

    def _resolve(self, reference: str) -> Any:
        if not reference.startswith("#/"):
            raise SchemaError(f"unsupported non-local schema reference: {reference}")
        value: Any = self.root
        for raw_token in reference[2:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            try:
                value = value[token]
            except (KeyError, TypeError) as exc:
                raise SchemaError(f"unresolvable schema reference: {reference}") from exc
        return value

    @staticmethod
    def _is_type(value: Any, expected: str) -> bool:
        checks = {
            "null": value is None,
            "boolean": isinstance(value, bool),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
        }
        return checks.get(expected, False)

    def _validate_object(self, instance: dict[str, Any], schema: dict[str, Any], path: str) -> None:
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                raise SchemaError(f"{path}: missing required property {key!r}")

        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            raise SchemaError(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise SchemaError(f"{path}: too many properties")

        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                self.validate(value, properties[key], child_path)
            elif additional is False:
                raise SchemaError(f"{path}: unexpected property {key!r}")
            elif isinstance(additional, dict):
                self.validate(value, additional, child_path)

    def _validate_array(self, instance: list[Any], schema: dict[str, Any], path: str) -> None:
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            normalized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(normalized) != len(set(normalized)):
                raise SchemaError(f"{path}: items are not unique")
        items = schema.get("items")
        if items is not None:
            for index, value in enumerate(instance):
                self.validate(value, items, f"{path}[{index}]")

    @staticmethod
    def _validate_string(instance: str, schema: dict[str, Any], path: str) -> None:
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise SchemaError(f"{path}: string shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaError(f"{path}: string longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                raise SchemaError(f"{path}: invalid schema pattern {schema['pattern']!r}") from exc
            if matched is None:
                raise SchemaError(f"{path}: string does not match required pattern")

    @staticmethod
    def _validate_number(instance: float, schema: dict[str, Any], path: str) -> None:
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaError(f"{path}: value below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaError(f"{path}: value above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            raise SchemaError(f"{path}: value not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            raise SchemaError(f"{path}: value not below exclusiveMaximum")


def pointer(document: Any, reference: str) -> Any:
    require(reference.startswith("/"), f"invalid document pointer: {reference}")
    value = document
    for raw_token in reference[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        try:
            value = value[token]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"missing required artifact path: {reference}") from exc
    return value


def verify_research_log() -> None:
    path = ROOT / "research" / "consulted-sources.md"
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationError("missing required file: research/consulted-sources.md") from exc

    access_date = re.search(
        r"\baccess(?:ed| date)?\s*(?::|on)?\s*20\d{2}-\d{2}-\d{2}\b",
        content,
        re.IGNORECASE,
    )
    require(access_date is not None, "research log must state an ISO access date")
    for raw_date in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", content):
        try:
            date.fromisoformat(raw_date)
        except ValueError as exc:
            raise VerificationError(f"research log has an invalid access date: {raw_date}") from exc

    lines = content.splitlines()
    url_locations: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines):
        url_locations.extend(
            (line_number, match)
            for match in re.findall(r"https?://[^\s)>\]]+", line)
        )
    require(url_locations, "research log must contain at least one consulted source URL")

    for line_number, raw_url in url_locations:
        url = raw_url.rstrip(".,;:")
        parsed_url = urlparse(url)
        hostname = (parsed_url.hostname or "").lower()
        official_source = (
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "vmware.com"
            or hostname.endswith(".vmware.com")
            or hostname == "vmware.github.io"
            or (hostname == "github.com" and parsed_url.path.startswith("/vmware/"))
        )
        require(
            official_source,
            f"research source is not Broadcom-published: {url}",
        )

        line = lines[line_number]
        before_url, after_url = line.split(raw_url, 1)
        previous = lines[line_number - 1] if line_number else ""
        following = lines[line_number + 1] if line_number + 1 < len(lines) else ""
        title_text = before_url if len(re.findall(r"[A-Za-z]", before_url)) >= 5 else previous
        decision_text = after_url if len(re.findall(r"[A-Za-z]", after_url)) >= 12 else following
        require(
            len(re.findall(r"[A-Za-z]", title_text)) >= 5,
            f"research source is missing a title: {url}",
        )
        require(
            len(re.findall(r"[A-Za-z]", decision_text)) >= 12,
            f"research source is missing the decision it informed: {url}",
        )


def verify_sddc_semantics(
    sddc: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    require(sddc.get("sddcId") == requirements["designId"], "SddcSpec has wrong sddcId")
    require(sddc.get("workflowType") == "VCF", "greenfield workflowType must be VCF")
    for component_path, expected in snapshot["greenfieldComponentVersions"].items():
        require(pointer(sddc, component_path) == expected, f"unsupported version at {component_path}")

    for key in (
        "vcenterSpec",
        "sddcManagerSpec",
        "nsxtSpec",
        "vspClusterSpec",
        "vcfOperationsSpec",
        "licenseServerSpec",
    ):
        require(sddc[key].get("useExistingDeployment") is False, f"{key} must be a new deployment")

    expected_hosts = {
        hostname
        for values in requirements["hosts"]["hostnamesBySite"].values()
        for hostname in values
    }
    actual_hosts = [host.get("hostname") for host in sddc.get("hostSpecs", [])]
    require(len(actual_hosts) == len(expected_hosts), "SddcSpec must contain every stated host once")
    require(set(actual_hosts) == expected_hosts, "SddcSpec host set does not match requirements")
    require(len(actual_hosts) == len(set(actual_hosts)), "SddcSpec contains duplicate hosts")

    require(sddc.get("dnsSpec") == requirements["dns"], "DNS specification does not match requirements")
    require(sddc.get("ntpServers") == requirements["ntpServers"], "NTP servers do not match requirements")
    require(sddc.get("clusterSpec") == {
        "clusterName": requirements["managementDomain"]["clusterName"],
        "datacenterName": requirements["managementDomain"]["datacenterName"],
    }, "cluster naming does not match requirements")

    expected_networks = {item["networkType"]: item for item in requirements["networks"]}
    actual_networks = {item.get("networkType"): item for item in sddc.get("networkSpecs", [])}
    require(
        len(sddc.get("networkSpecs", [])) == len(actual_networks),
        "SddcSpec contains duplicate network types",
    )
    require(set(actual_networks) == set(expected_networks), "network types do not match requirements")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for field in ("vlanId", "subnet", "gateway", "mtu"):
            require(actual.get(field) == expected[field], f"{network_type} has wrong {field}")
        require(actual.get("includeIpAddressRanges") == [{
            "startIpAddress": expected["startIpAddress"],
            "endIpAddress": expected["endIpAddress"],
        }], f"{network_type} has wrong host IP range")

    appliances = requirements["appliances"]
    require(sddc["vcenterSpec"]["vcenterHostname"] == appliances["vcenterHostname"], "wrong vCenter FQDN")

    password_values: list[tuple[str, Any]] = []

    def collect_passwords(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if "password" in key.lower():
                    password_values.append((child_path, child))
                collect_passwords(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect_passwords(child, f"{path}[{index}]")

    collect_passwords(sddc)
    require(password_values, "SddcSpec must provide credentials as environment placeholders")
    for password_path, value in password_values:
        require(
            isinstance(value, str)
            and re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", value) is not None,
            f"password at {password_path} must be an environment-variable placeholder",
        )
    require(sddc["sddcManagerSpec"]["hostname"] == appliances["sddcManagerHostname"], "wrong SDDC Manager FQDN")
    nsx_hostnames = [item.get("hostname") for item in sddc["nsxtSpec"]["nsxtManagers"]]
    require(
        len(nsx_hostnames) == len(set(nsx_hostnames))
        and set(nsx_hostnames) == set(appliances["nsxManagerHostnames"]),
        "wrong NSX manager set",
    )
    require(sddc["nsxtSpec"]["vipFqdn"] == appliances["nsxVipFqdn"], "wrong NSX VIP")
    operations_hostnames = [item.get("hostname") for item in sddc["vcfOperationsSpec"]["nodes"]]
    require(
        len(operations_hostnames) == len(set(operations_hostnames))
        and set(operations_hostnames) == set(appliances["operationsHostnames"]),
        "wrong Operations nodes",
    )
    require(sddc["licenseServerSpec"]["hostname"] == appliances["licenseServerHostname"], "wrong license server")

    management = requirements["managementServices"]
    require(sddc["vspClusterSpec"]["ipv4Pool"].get("addresses") == management["ipv4Addresses"], "management-services IP pool must contain the stated 12 addresses")
    require(sddc["vspClusterSpec"].get("internalClusterCidrIpv4") == management["internalClusterCidrIpv4"], "wrong management-services internal CIDR")
    local_network = sddc["vcfManagementComponentsInfrastructureSpec"].get("localRegionNetwork")
    require(local_network == {
        "networkName": management["networkName"],
        "gateway": management["gateway"],
        "subnetMask": management["subnetMask"],
    }, "wrong management-components network")


def verify_site_design(
    design: dict[str, Any], sddc: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    require(design["designId"] == requirements["designId"], "site design has wrong designId")
    require(design["targetRelease"] == requirements["targetRelease"], "site design has wrong target release")
    expected_installer = {key: snapshot["installerOpenApi"][key] for key in ("repositoryTag", "path", "schema")}
    require(design["installerSchema"] == expected_installer, "site design identifies the wrong installer schema")

    domain = design["managementDomain"]
    domain_req = requirements["managementDomain"]
    require(domain["topology"] == domain_req["topology"], "management domain must be stretched")
    require(domain["clusterName"] == domain_req["clusterName"], "wrong management cluster name")
    require(domain["siteFailureTolerance"] == domain_req["siteFailureTolerance"], "wrong site failure tolerance")
    require(domain["additionalHostFailureAfterSiteFailure"] == domain_req["additionalHostFailureAfterSiteFailure"], "wrong post-site-failure host tolerance")

    expected_data = requirements["sites"]["dataSites"]
    expected_data_ids = [site["siteId"] for site in expected_data]
    witness_req = requirements["sites"]["witnessSite"]
    require(set(domain["dataSiteIds"]) == set(expected_data_ids), "wrong data sites")
    witness = domain["witness"]
    require(witness["siteId"] == witness_req["siteId"], "witness is not at the required third site")
    require(witness["siteId"] not in domain["dataSiteIds"], "witness cannot be in a data site")
    require(witness["failureDomain"] == witness_req["failureDomain"], "witness is in the wrong failure domain")
    require(witness["applianceVersion"] == snapshot["witnessApplianceVersion"], "unsupported witness appliance version")
    require(witness["hostsWorkloads"] is False, "witness must not host workloads")

    expected_sites = {
        site["siteId"]: ("data", site["failureDomain"]) for site in expected_data
    }
    expected_sites[witness_req["siteId"]] = ("witness", witness_req["failureDomain"])
    actual_sites = {site["siteId"]: (site["kind"], site["failureDomain"]) for site in design["sites"]}
    require(actual_sites == expected_sites, "site/failure-domain inventory is incorrect")
    require(len({value[1] for value in actual_sites.values()}) == 3, "all sites must use distinct failure domains")

    placements = domain["hostPlacement"]
    expected_by_site = requirements["hosts"]["hostnamesBySite"]
    require({item["hostname"] for item in placements} == {host["hostname"] for host in sddc["hostSpecs"]}, "site placements and SddcSpec hosts differ")
    require(len(placements) == len({item["hostname"] for item in placements}), "duplicate host placement")
    per_host = requirements["hosts"]["perHost"]
    for placement in placements:
        require(placement["hostname"] in expected_by_site.get(placement["siteId"], []), f"host {placement['hostname']} placed at wrong site")
        for field in ("cores", "memoryGiB", "rawStorageTiB"):
            require(placement[field] == per_host[field], f"host {placement['hostname']} has wrong {field}")
    for site_id, hostnames in expected_by_site.items():
        require(sum(item["siteId"] == site_id for item in placements) == requirements["hosts"]["perDataSite"], f"{site_id} has wrong host count")
        require({item["hostname"] for item in placements if item["siteId"] == site_id} == set(hostnames), f"{site_id} host placement mismatch")

    total_hosts = len(placements)
    site_hosts = requirements["hosts"]["perDataSite"]
    derived_capacity = {
        "total": {
            "cores": total_hosts * per_host["cores"],
            "memoryGiB": total_hosts * per_host["memoryGiB"],
            "rawStorageTiB": total_hosts * per_host["rawStorageTiB"],
        },
        "survivingSite": {
            "cores": site_hosts * per_host["cores"],
            "memoryGiB": site_hosts * per_host["memoryGiB"],
            "rawStorageTiB": site_hosts * per_host["rawStorageTiB"],
        },
        "survivingSiteAfterAdditionalHostFailure": {
            "cores": (site_hosts - 1) * per_host["cores"],
            "memoryGiB": (site_hosts - 1) * per_host["memoryGiB"],
            "rawStorageTiB": (site_hosts - 1) * per_host["rawStorageTiB"],
        },
        "requiredAfterFailures": requirements["requiredCapacityAfterFailures"],
    }
    require(design["capacity"] == derived_capacity, "capacity calculations are incorrect")
    post_failure = derived_capacity["survivingSiteAfterAdditionalHostFailure"]
    required = derived_capacity["requiredAfterFailures"]
    require(post_failure["cores"] >= required["cores"], "insufficient compute after stated failures")
    require(post_failure["memoryGiB"] >= required["memoryGiB"], "insufficient memory after stated failures")
    require(post_failure["rawStorageTiB"] >= required["rawStorageTiB"], "insufficient raw storage after stated failures")

    link_req = requirements["links"]
    links = design["links"]
    require(links == {
        "dataSiteRttMs": link_req["designedDataSiteRttMs"],
        "witnessRttMs": link_req["designedWitnessRttMs"],
        "dataSiteBandwidthGbps": link_req["designedDataSiteBandwidthGbps"],
        "witnessBandwidthMbps": link_req["designedWitnessBandwidthMbps"],
    }, "link design does not use the stated values")
    require(links["dataSiteRttMs"] <= link_req["maximumDataSiteRttMs"], "data-site RTT exceeds requirement")
    require(links["witnessRttMs"] <= link_req["maximumWitnessRttMs"], "witness RTT exceeds requirement")
    require(links["dataSiteBandwidthGbps"] >= link_req["minimumDataSiteBandwidthGbps"], "data-site bandwidth is insufficient")
    require(links["witnessBandwidthMbps"] >= link_req["minimumWitnessBandwidthMbps"], "witness bandwidth is insufficient")


def verify_migration(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    require(plan["estateId"] == inventory["estateId"], "migration plan has wrong estateId")
    require(plan["targetRelease"] == snapshot["targetRelease"], "migration plan has wrong target")
    components = {item["componentId"]: item for item in inventory["components"]}
    transitions = snapshot["supportedUpgradeTransitions"]
    steps = plan["steps"]
    require(len(steps) == len(components) == len(transitions), "migration must cover every component exactly once")
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "migration order must be contiguous")
    require(len({step["componentId"] for step in steps}) == len(steps), "migration repeats a component")
    require({step["componentId"] for step in steps} == set(components), "migration component coverage differs from inventory")

    for step, transition in zip(steps, transitions):
        component = components[transition["componentId"]]
        expected = {
            "order": transition["order"],
            "componentId": transition["componentId"],
            "componentName": component["name"],
            "currentVersion": component["version"],
            "targetVersion": transition["targetVersion"],
            "action": transition["action"],
            "method": transition["method"],
            "gates": transition["requiredGates"],
        }
        require(step == expected, f"unsupported transition or gates at migration step {transition['order']}")
        require(transition["fromVersion"] == component["version"], f"snapshot/inventory source mismatch for {component['name']}")


def verify_stdlib_package() -> None:
    package = ROOT / "vcf_architecture"
    require((package / "__init__.py").is_file(), "vcf_architecture is not a Python package")
    require((package / "__main__.py").is_file(), "package is not runnable with python -m")
    python_files = sorted(package.rglob("*.py"))
    require(python_files, "Python package contains no source files")
    allowed_roots = set(sys.stdlib_module_names) | {"vcf_architecture"}
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                root_name = name.split(".", 1)[0]
                require(root_name in allowed_roots, f"non-stdlib import {name!r} in {path.relative_to(ROOT)}")


def verify_reproduction(expected: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        output = Path(temp_dir) / "artifacts"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "vcf_architecture", "--output", str(output)],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
        require(result.returncode == 0, f"package execution failed: {result.stderr.strip()}")
        for filename, expected_document in expected.items():
            actual = load_json(output / filename)
            require(actual == expected_document, f"package did not deterministically reproduce {filename}")


def main() -> None:
    # The tagged installer's own SddcSpec schema is deliberately the first
    # artifact validation performed. All scenario-specific checks follow it.
    snapshot = load_json(ROOT / "fixtures" / "compatibility-snapshot.json")
    openapi_path = ROOT / snapshot["installerOpenApi"]["path"]
    openapi_bytes = openapi_path.read_bytes()
    actual_digest = hashlib.sha256(openapi_bytes).hexdigest()
    require(actual_digest == snapshot["installerOpenApi"]["sha256"], "pinned installer OpenAPI digest mismatch")
    openapi = json.loads(openapi_bytes)
    sddc = load_json(ROOT / "artifacts" / "sddc-spec.json")
    sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    Validator(openapi).validate(sddc, sddc_schema)

    requirements = load_json(ROOT / "fixtures" / "design-requirements.json")
    inventory = load_json(ROOT / "fixtures" / "estate-inventory.json")
    site_design = load_json(ROOT / "artifacts" / "site-design.json")
    migration = load_json(ROOT / "artifacts" / "migration-plan.json")
    site_schema = load_json(ROOT / "specifications" / "site-design.schema.json")
    migration_schema = load_json(ROOT / "specifications" / "migration-plan.schema.json")
    Validator(site_schema).validate(site_design, site_schema)
    Validator(migration_schema).validate(migration, migration_schema)

    verify_sddc_semantics(sddc, requirements, snapshot)
    verify_site_design(site_design, sddc, requirements, snapshot)
    verify_migration(migration, inventory, snapshot)
    verify_research_log()
    verify_stdlib_package()
    verify_reproduction({
        "sddc-spec.json": sddc,
        "site-design.json": site_design,
        "migration-plan.json": migration,
    })
    print("PASS: VCF 9.1 architecture artifacts are valid and reproducible")


if __name__ == "__main__":
    try:
        main()
    except (VerificationError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
