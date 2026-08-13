package contracttest

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"example.com/vcf/restoredrill/drill"
)

// TestContractIsAuthoritative moves the operations to different paths, changes
// the status one of them answers with and withdraws the correlation header from
// the operation that declared it. The mock is rebuilt from the altered
// contract, so a drill that reads the contract at run time still works and one
// that hard-codes the routes does not.
func TestContractIsAuthoritative(t *testing.T) {
	t.Parallel()

	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read %s: %v", contractPath, err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse %s: %v", contractPath, err)
	}

	operations, ok := doc["operations"].(map[string]any)
	if !ok {
		t.Fatalf("%s has no operations object", contractPath)
	}
	op := func(name string) map[string]any {
		entry, ok := operations[name].(map[string]any)
		if !ok {
			t.Fatalf("%s does not describe operation %q", contractPath, name)
		}
		return entry
	}

	op("getComponents")["path"] = "/v1/inventory/components"
	op("getTask")["path"] = "/v1/operations/{taskId}"
	op("backupRestoreComponentsAction")["successStatus"] = 201
	op("backupRestoreComponentsAction")["optionalHeaders"] = []any{}

	altered := filepath.Join(t.TempDir(), "contract.json")
	edited, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		t.Fatalf("encode altered contract: %v", err)
	}
	if err := os.WriteFile(altered, edited, 0o644); err != nil {
		t.Fatalf("write altered contract: %v", err)
	}

	server := startMock(t, mockWithContract(altered))
	planPath := writePlan(t, map[string]any{
		"scope":         "FLEET",
		"correlationId": "drill-2026-02-11",
		"components":    []any{map[string]any{"componentType": "vidb"}},
	})

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	report, err := drill.Run(ctx, drill.Options{
		PlanPath:     planPath,
		ContractPath: altered,
		BaseURL:      server.URL,
		Token:        token,
		PollInterval: 2 * time.Millisecond,
		PollTimeout:  30 * time.Second,
	})
	if err != nil {
		t.Fatalf("the drill did not follow the contract it was given: %v", err)
	}

	requireNoViolations(t, server)
	requireAuthorized(t, server)

	if got := report.Outcome; got != "succeeded" {
		t.Errorf("outcome is %q, want %q", got, "succeeded")
	}
	if got := s0(t, server, "getComponents").Path; got != "/v1/inventory/components" {
		t.Errorf("getComponents went to %q, want the path the contract publishes", got)
	}
	if got := s0(t, server, "getTask").Path; !strings.HasPrefix(got, "/v1/operations/") {
		t.Errorf("getTask went to %q, want the path the contract publishes", got)
	}
	// The altered contract withdraws the header from the only operation that
	// declared it, so it must not be sent even though the plan supplies one.
	requireNoHeaderAnywhere(t, server, "X-Correlation-Id")
}
