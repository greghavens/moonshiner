#!/bin/sh
# Protected acceptance verifier for the SDDC LCM support-bundle client.
#
# Compiles the loopback mock, the wire-shape verifier and the deliverable, then runs
# the verifier. Everything happens on 127.0.0.1; no live VMware endpoint is contacted.
#
# PROTECTED -- do not modify this file.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

if [ ! -f SddcLcmClient.java ]; then
    echo "FAILED: SddcLcmClient.java is missing from the workspace root." >&2
    exit 1
fi

# The client is specified as a single compilation unit. Anything else outside
# .protected/ means the implementation was split across files.
extra=$(find . -name '*.java' \
    -not -path './.protected/*' \
    -not -path './_verification/*' \
    -not -name 'SddcLcmClient.java' | sort)
if [ -n "$extra" ]; then
    echo "FAILED: the client must live entirely in SddcLcmClient.java; also found:" >&2
    echo "$extra" >&2
    exit 1
fi

out=_verification/classes
rm -rf _verification
mkdir -p "$out"

javac -d "$out" \
    .protected/MockSddcLcm.java \
    .protected/TestMain.java \
    SddcLcmClient.java

exec java -cp "$out" TestMain
