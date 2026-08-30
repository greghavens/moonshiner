#!/usr/bin/env bash
# Protected verification for the SDDC Manager 9.0 client exercise.
#
# Compiles the client together with the harness, runs it against the loopback mock, then checks the
# recorded request log, the client's result, and the two files under docs/. Nothing outside this
# directory is contacted: the mock binds 127.0.0.1 and the verifier reads only local files.
set -euo pipefail
cd "$(dirname "$0")"

rm -rf build target
mkdir -p build target

echo "== compiling =="
mapfile -t client_sources < <(find src -type f -name '*.java' -print | sort)
if [[ ${#client_sources[@]} -ne 1 || ${client_sources[0]} != src/com/example/vcf/SddcManagerClient.java ]]; then
  echo "The client must stay in src/com/example/vcf/SddcManagerClient.java and no other Java source file."
  exit 1
fi
find harness src -name '*.java' -print0 | xargs -0 javac -d build

echo "== running the client against the loopback mock =="
java -cp build com.example.vcf.harness.TestMain target
for terminal_status in FAILED CANCELLED COMPLETED_WITH_WARNING SKIPPED; do
  java -cp build com.example.vcf.harness.TestMain "target/terminal-$terminal_status" "$terminal_status"
done

echo "== verifying =="
java -cp build com.example.vcf.harness.Verifier target docs
