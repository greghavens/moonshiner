#!/usr/bin/env bash
set -euo pipefail

# The installer's own SddcSpec schema is deliberately the first acceptance gate.
go test -race ./grader_tests -run '^TestArtifactSddcSchema$' -count=1
go test -race ./... -count=1
