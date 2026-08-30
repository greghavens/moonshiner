#!/usr/bin/env bash
set -euo pipefail

workspace_root=$(cd "$(dirname "$0")/.." && pwd)
build_dir=$(mktemp -d)
trap 'rm -rf -- "$build_dir"' EXIT

python3 -B - "$workspace_root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
contract = json.loads((root / "docs/contract.json").read_text())
sources = json.loads((root / "docs/official_sources.json").read_text())

sha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
path = "specifications/vcf-installer/vcf-installer-openapi.json"

assert contract["apiVersion"] == "9.1.0.0"
assert contract["source"]["commitSha"] == sha
assert contract["source"]["specPath"] == path
assert set(contract["operations"]) == {"getTasks"}
operation = contract["operations"]["getTasks"]
assert (operation["operationId"], operation["method"], operation["path"]) == (
    "getTasks", "GET", "/v1/tasks")
assert [item["name"] for item in operation["parameters"]] == [
    "limit", "taskStatus", "taskType", "resourceId", "resourceType",
    "completedAfter", "pageNumber", "pageSize", "orderDirection", "orderBy",
    "taskName", "doLiveRefresh",
]
assert operation["responses"]["200"]["$ref"] == "#/schemas/PageOfTask"
assert contract["schemas"]["Task"]["required"] == [
    "creationTimestamp", "id", "name", "status"]
assert set(contract["schemas"]["PageMetadata"]["properties"]) == {
    "pageNumber", "pageSize", "totalElements", "totalPages"}

assert sources["repository"] == "https://github.com/vmware/vcf-api-specs"
assert sources["repositoryLicense"] == "Apache-2.0"
assert sources["repositoryCommitSha"] == sha
assert sources["specPath"] == path
assert sources["operations"] == [{
    "operationId": "getTasks", "method": "GET", "path": "/v1/tasks"}]
assert sha in sources["specUrl"] and sources["specUrl"].endswith(path)
PY

javac --release 17 --add-modules jdk.httpserver \
  -d "$build_dir" \
  "$workspace_root/VcfInstallerClient.java" \
  "$workspace_root/TestMain.java"

java --add-modules jdk.httpserver -cp "$build_dir" TestMain "$workspace_root"
