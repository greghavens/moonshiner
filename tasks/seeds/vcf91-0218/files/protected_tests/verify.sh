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

sha = "c3f3b52c845dd967cabbc21680e893292077d5ba"
path = "specifications/vcf-installer/vcf-installer-openapi.json"
operation_id = "updateDepotSettings"

assert contract["contractFormat"] == "focused-openapi-projection-v1"
assert contract["apiVersion"] == "9.1.0.0"
assert contract["openapiVersion"] == "3.0.1"
assert contract["source"] == {
    "repository": "https://github.com/vmware/vcf-api-specs",
    "repositoryCommitSha": sha,
    "specPath": path,
    "license": "Apache-2.0",
}
assert set(contract["operations"]) == {operation_id}
operation = contract["operations"][operation_id]
assert (operation["operationId"], operation["method"], operation["path"]) == (
    operation_id, "PUT", "/v1/system/settings/depot")
assert operation["parameters"] == []
assert operation["requestBody"] == {
    "required": True,
    "contentType": "application/json",
    "schema": "#/schemas/DepotSettings",
}
assert set(operation["responses"]) == {"202", "400", "500"}
assert operation["responses"]["202"]["schema"] == "#/schemas/DepotSettings"
assert operation["responses"]["500"]["schema"] == "#/schemas/Error"

account = contract["schemas"]["DepotAccount"]
assert account["required"] == []
assert list(account["properties"]) == [
    "username", "password", "status", "message",
    "downloadToken", "downloadActivationCode",
]
assert account["properties"]["downloadToken"]["maxLength"] == 32
settings = contract["schemas"]["DepotSettings"]
assert settings["required"] == []
assert list(settings["properties"]) == [
    "vmwareAccount", "offlineAccount", "depotConfiguration"]

assert sources["repository"] == "vmware/vcf-api-specs"
assert sources["repositoryUrl"] == "https://github.com/vmware/vcf-api-specs"
assert sources["repositoryLicense"] == "Apache-2.0"
assert sources["repositoryCommitSha"] == sha
assert sources["specPath"] == path
assert sources["operationIds"] == [operation_id]
assert sources["operations"] == [{
    "operationId": operation_id,
    "method": "PUT",
    "path": "/v1/system/settings/depot",
    "repositoryCommitSha": sha,
    "specPath": path,
    "specJsonPointer": "/paths/~1v1~1system~1settings~1depot/put/operationId",
}]
assert sha in sources["specUrl"] and sources["specUrl"].endswith(path)
assert sources["derivation"]["documentationPageUsedAsContractSource"] is False
PY

javac --release 17 --add-modules jdk.httpserver \
  -d "$build_dir" \
  "$workspace_root/VcfInstallerClient.java" \
  "$workspace_root/TestMain.java"

java --add-modules jdk.httpserver -cp "$build_dir" \
  TestMain "$workspace_root" "$build_dir/request-log.jsonl"
