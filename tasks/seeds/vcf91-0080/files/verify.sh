#!/usr/bin/env bash
set -euo pipefail

task_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
build_dir=$(mktemp -d "${TMPDIR:-/tmp}/vcf91-0080.XXXXXX")
cleanup() {
  rm -rf -- "$build_dir"
}
trap cleanup EXIT HUP INT TERM

required=(
  "$task_root/src/NsxPolicyClient.java"
  "$task_root/test/ContractMockServer.java"
  "$task_root/test/TestMain.java"
  "$task_root/docs/contract.json"
  "$task_root/docs/official_sources.json"
)
for path in "${required[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "missing required file: ${path#"$task_root"/}" >&2
    exit 1
  fi
done

python3 - "$task_root/docs/contract.json" "$task_root/docs/official_sources.json" <<'PY'
import json
import sys

contract_path, sources_path = sys.argv[1:]
with open(contract_path, encoding="utf-8") as handle:
    contract = json.load(handle)
with open(sources_path, encoding="utf-8") as handle:
    sources = json.load(handle)

expected_ids = ["PatchGroupForDomain", "ReadGroupForDomain"]
actual_ids = [item["operationId"] for item in contract["operations"]]
if actual_ids != expected_ids:
    raise SystemExit(f"contract operationIds changed: {actual_ids!r}")
if sources["operation_ids"] != expected_ids:
    raise SystemExit(f"official source operationIds changed: {sources['operation_ids']!r}")
if contract["basePath"] != "/policy/api/v1":
    raise SystemExit(f"contract basePath changed: {contract['basePath']!r}")
if contract["source"]["commit"] != sources["repository_commit_sha"]:
    raise SystemExit("contract and official source commits differ")
if contract["source"]["path"] != sources["spec_path"]:
    raise SystemExit("contract and official source specification paths differ")
PY

javac --release 17 -encoding UTF-8 \
  -d "$build_dir" \
  "$task_root/src/NsxPolicyClient.java" \
  "$task_root/test/ContractMockServer.java" \
  "$task_root/test/TestMain.java"

java -cp "$build_dir" TestMain \
  "$task_root/docs/contract.json" \
  "$task_root/docs/official_sources.json"
