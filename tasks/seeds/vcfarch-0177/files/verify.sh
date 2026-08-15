#!/usr/bin/env bash
set -euo pipefail

# Schema validation is deliberately isolated and always runs before any package
# or semantic acceptance check.
go test -race ./verifier/schema -run '^TestArtifactMatchesInstallerSchema$' -count=1
go test -race ./migrationplan -count=1
go test -race ./verifier -run '^Test(PlanSemantics|BuildRejectsInvalidInputs|BuildRejectsMissingInputs|ResearchRecord|PackageContainsTableDrivenTests)$' -count=1
