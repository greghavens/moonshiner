#!/usr/bin/env bash
set -euo pipefail

# The grader's first operation is validation of sddcSpec against the pinned
# installer schema. Package-authored tests run only after the protected grader.
go test -race ./grader
go test -race ./architecture
