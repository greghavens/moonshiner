#!/usr/bin/env bash
set -euo pipefail

workspace_root=$(cd "$(dirname "$0")/.." && pwd)

python3 -B - "$workspace_root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
contract = json.loads((root / "docs/contract.json").read_text())
sources = json.loads((root / "docs/official_sources.json").read_text())

sha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
path = "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"
operation_ids = [
    "Snapservice.Sessions_create",
    "Snapservice.Clusters.ProtectionGroups_list",
    "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task",
    "Snapservice.Tasks_get",
]

assert contract["apiTitle"] == "Snapshot Appliance API"
assert contract["apiVersion"] == "9.1.0.0"
assert contract["source"]["repository"] == "https://github.com/vmware/vcf-api-specs"
assert contract["source"]["commitSha"] == sha
assert contract["source"]["specPath"] == path
assert contract["securityScheme"]["type"] == "apiKey"
assert contract["securityScheme"]["in"] == "header"
assert contract["securityScheme"]["header"] == "vmware-api-session-id"
assert list(contract["operations"]) == operation_ids

expected = {
    "Snapservice.Sessions_create": ("POST", "/snapservice/sessions"),
    "Snapservice.Clusters.ProtectionGroups_list": (
        "GET", "/snapservice/clusters/{cluster}/protection-groups"),
    "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task": (
        "POST", "/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots"),
    "Snapservice.Tasks_get": ("GET", "/snapservice/tasks/{task}"),
}
for operation_id, (method, op_path) in expected.items():
    operation = contract["operations"][operation_id]
    assert operation["operationId"] == operation_id
    assert (operation["method"], operation["path"]) == (method, op_path)
    assert operation["security"] == ["api_key_auth"]

create = contract["operations"][
    "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"]
assert create["fixedQuery"] == {"vmw-task": "true"}
assert create["specPathKey"].endswith("?vmw-task=true")
assert create["requestBody"]["contentType"] == "application/json"
assert create["requestBody"]["$ref"].endswith(
    "Snapservice.Clusters.ProtectionGroups.Snapshots.CreateSpec")
assert create["responses"]["202"] == {"type": "string"}

listing = contract["operations"]["Snapservice.Clusters.ProtectionGroups_list"]
assert [p["name"] for p in listing["parameters"]] == [
    "cluster", "pgs", "names", "states", "vms", "cluster_pairs"]
for parameter in listing["parameters"]:
    if parameter["in"] == "query":
        assert parameter["required"] is False
        assert (parameter["style"], parameter["explode"]) == ("form", True)

spec_schema = contract["schemas"][
    "Snapservice.Clusters.ProtectionGroups.Snapshots.CreateSpec"]
assert spec_schema["required"] == ["name"]
assert list(spec_schema["properties"]) == ["name", "retention"]
retention = contract["schemas"]["Snapservice.RetentionPeriod"]
assert retention["required"] == ["duration", "unit"]
assert list(retention["properties"]) == ["unit", "duration"]
assert contract["schemas"]["Snapservice.Tasks.Status"]["enum"] == [
    "PENDING", "RUNNING", "BLOCKED", "SUCCEEDED", "FAILED"]

assert sources["repository"] == "https://github.com/vmware/vcf-api-specs"
assert sources["repositoryLicense"] == "Apache-2.0"
assert sources["repositoryCommitSha"] == sha
assert sources["specPath"] == path
assert sha in sources["specUrl"] and sources["specUrl"].endswith(path)
assert [item["operationId"] for item in sources["operations"]] == operation_ids
for item in sources["operations"]:
    method, op_path = expected[item["operationId"]]
    assert (item["method"], item["path"]) == (method, op_path)
PY

cd "$workspace_root"
export GOFLAGS=-mod=mod
export GOTOOLCHAIN=local
export GOPROXY=off
export GOWORK=off

go vet ./...
go test -race -count=1 ./...
