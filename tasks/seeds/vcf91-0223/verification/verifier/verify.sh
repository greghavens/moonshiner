#!/usr/bin/env bash
set -euo pipefail

workspace_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$workspace_root"

for tool in python3 pwsh; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "verify: required tool '$tool' is not on PATH" >&2
    exit 2
  fi
done

# ---------------------------------------------------------------------------
# 1. the pinned contract and its provenance
# ---------------------------------------------------------------------------
python3 -B - "$workspace_root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
contract = json.loads((root / "docs/contract.json").read_text())
sources = json.loads((root / "docs/official_sources.json").read_text())

sha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
blob = "fa97e0975ac108c81173b5bdd4fde57f20b2e190"
path = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"

assert contract["apiVersion"] == "9.1.0.0", contract["apiVersion"]
assert contract["openapiVersion"] == "3.0.4", contract["openapiVersion"]
assert contract["source"]["repository"] == "https://github.com/vmware/vcf-api-specs"
assert contract["source"]["commitSha"] == sha
assert contract["source"]["specPath"] == path
assert contract["source"]["specBlobSha"] == blob

assert set(contract["operations"]) == {"getComponents", "getComponentNodes"}

components = contract["operations"]["getComponents"]
assert (components["operationId"], components["method"], components["path"]) == (
    "getComponents", "GET", "/v1/components")
assert [p["name"] for p in components["parameters"]] == ["scope"]
assert components["parameters"][0]["enum"] == ["FLEET", "INSTANCE"]
assert components["parameters"][0]["required"] is False
assert components["queryParameterOrder"] == ["scope"]
assert components["responses"]["200"]["$ref"] == "#/schemas/Components"

nodes = contract["operations"]["getComponentNodes"]
assert (nodes["operationId"], nodes["method"], nodes["path"]) == (
    "getComponentNodes", "GET", "/v1/components/{componentId}/nodes")
assert [p["name"] for p in nodes["parameters"]] == [
    "componentId", "pageNumber", "pageSize", "nodeTypes"]
assert nodes["parameters"][0]["in"] == "path" and nodes["parameters"][0]["required"] is True
assert all(p["required"] is False for p in nodes["parameters"][1:])
assert nodes["queryParameterOrder"] == ["pageNumber", "pageSize", "nodeTypes"]
assert nodes["responses"]["200"]["$ref"] == "#/schemas/Nodes"
assert nodes["responses"]["404"]["$ref"] == "#/schemas/ErrorResponse"

assert contract["security"]["scheme"] == "bearerToken"
assert contract["security"]["httpScheme"] == "Bearer"

assert contract["pagination"]["operationId"] == "getComponentNodes"
assert contract["pagination"]["responseElementsField"] == "nodes"
assert contract["pagination"]["responseMetadataField"] == "pageMetadata"
assert contract["pagination"]["firstPageNumber"] == 0
assert set(contract["pagination"]["metadataFields"]) == {
    "pageNumber", "pageSize", "totalElements", "totalPages"}

assert set(contract["schemas"]["PageMetadata"]["properties"]) == {
    "pageNumber", "pageSize", "totalElements", "totalPages"}
assert set(contract["schemas"]["Node"]["properties"]) == {
    "nodeType", "id", "version", "fqdn", "ipAddress", "status", "name", "size"}
assert set(contract["schemas"]["Nodes"]["properties"]) == {"nodes", "pageMetadata"}
assert set(contract["schemas"]["Components"]["properties"]) == {"components"}
assert contract["schemas"]["Component"]["properties"]["scope"]["enum"] == ["FLEET", "INSTANCE"]
assert contract["schemas"]["ErrorResponse"]["required"] == [
    "code", "message", "referenceId", "resolution", "timestamp"]

assert sources["repository"] == "https://github.com/vmware/vcf-api-specs"
assert sources["repositoryLicense"] == "Apache-2.0"
assert sources["repositoryCommitSha"] == sha
assert sources["specPath"] == path
assert sources["specBlobSha"] == blob
assert sha in sources["specUrl"] and sources["specUrl"].endswith(path)
assert sources["operations"] == [
    {"operationId": "getComponents", "method": "GET", "path": "/v1/components"},
    {"operationId": "getComponentNodes", "method": "GET",
     "path": "/v1/components/{componentId}/nodes"},
]
assert sources["clientSdk"]["name"] == "VMware.Sdk.Vcf.Installer"
print("contract and provenance: ok")
PY

# ---------------------------------------------------------------------------
# 2. the VMware SDK is a prerequisite, never vendored into the workspace
# ---------------------------------------------------------------------------
vendored=$(find "$workspace_root" \
  \( -iname 'VMware.Sdk.Vcf*' -o -iname 'VMware.OpenAPI*' -o -iname 'VMware.Vim*' \
     -o -iname 'VMware.VimAutomation*' -o -iname '*.nupkg' \) -print 2>/dev/null || true)
if [ -n "$vendored" ]; then
  echo "verify: the VMware PowerCLI SDK must not be vendored into the workspace:" >&2
  echo "$vendored" >&2
  exit 1
fi
echo "no vendored VMware SDK payload: ok"

if ! pwsh -NoProfile -NonInteractive -Command \
  'if (-not (Get-Module -ListAvailable -Name VMware.Sdk.Vcf.Installer)) { exit 1 }'; then
  echo "verify: the prerequisite module VMware.Sdk.Vcf.Installer is not installed." >&2
  echo "        Install it with: Install-Module VMware.Sdk.Vcf.Installer -Scope CurrentUser" >&2
  exit 2
fi
echo "prerequisite module VMware.Sdk.Vcf.Installer present: ok"

# ---------------------------------------------------------------------------
# 3. behaviour and wire shape against the contract-pinned loopback mock
# ---------------------------------------------------------------------------
exec pwsh -NoProfile -NonInteractive -File "$workspace_root/tests/Invoke-ContractTests.ps1" \
  -WorkspaceRoot "$workspace_root"
