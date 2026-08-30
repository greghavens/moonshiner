#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

contract_sha="ff9d4c3f5f6f80411d5f1d2299060ad8f487d3d84bc898c464c1b9d68210e700"
sources_sha="2abc48272be97c78da607a80f5c1d506b9addbc54d3ad2a0fbf5d2037888b3b5"
mock_sha="2e975cff419ab2df7bb7c23f0e60c8c848d607d872e21841a00e40d893d7d9cb"
test_sha="4587bffc60d4ed61ffaa2c8481c491dfd973e03fa4016eed6939069a325008ba"

printf '%s  %s\n' "$contract_sha" docs/contract.json | sha256sum -c --quiet
printf '%s  %s\n' "$sources_sha" docs/official_sources.json | sha256sum -c --quiet
printf '%s  %s\n' "$mock_sha" mock/vcfa_mock.py | sha256sum -c --quiet
printf '%s  %s\n' "$test_sha" tests/TestMain.java | sha256sum -c --quiet

mapfile -t production_sources < <(
  find . -path './tests' -prune -o -name '*.java' -print | sort
)
if (( ${#production_sources[@]} != 1 )) || [[ ${production_sources[0]} != "./VcfaChangeClient.java" ]]; then
  printf '%s\n' 'deliverable must contain only VcfaChangeClient.java as production Java source' >&2
  exit 1
fi

build_dir=$(mktemp -d)
cleanup() {
  rm -rf -- "$build_dir"
}
trap cleanup EXIT

javac -encoding UTF-8 -d "$build_dir" VcfaChangeClient.java tests/TestMain.java
java -cp "$build_dir" TestMain "$repo_root" "$build_dir"
