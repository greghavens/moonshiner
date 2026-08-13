#!/bin/sh
# Compiles the client together with the harness, runs the scenario against the loopback mock
# appliance, then verifies the recorded requests. No network access and no VMware endpoint are
# involved. Exits 0 only when every check passes.
set -eu

cd "$(dirname "$0")"

rm -rf build out
mkdir -p build out

echo "== compiling =="
javac --release 17 -nowarn -d build \
    harness/MiniJson.java \
    harness/Fixture.java \
    harness/MockSddcManager.java \
    harness/TestMain.java \
    src/VcfNetworkPoolClient.java \
    verify/VerifyWireShape.java

echo "== running the scenario =="
java -cp build TestMain

echo "== verifying =="
java -cp build VerifyWireShape
