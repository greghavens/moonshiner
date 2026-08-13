// Package contracttest drives the fleet install run against a loopback mock of
// the VMware Cloud Foundation 9.1 SDDC LCM service and inspects every request it
// made. The mock builds its routes from docs/contract.json, so the contract the
// run was built against is exercised as well as the run itself.
//
// No VMware endpoint is contacted.
package contracttest

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"example.com/vcf/fleetlcm/fleetrun"
	"example.com/vcf/fleetlcm/internal/mocklcm"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"
	planPath     = "../fixtures/install-plan.json"

	taskID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
)

// pollInterval keeps the tests quick; the run under test must honour it.
const pollInterval = time.Millisecond

// seqCredentials hands out access tokens in order and moves to the next one
// when the service rejects the one in hand. It records how often that happened
// so a run that refreshes when it did not need to is visible.
type seqCredentials struct {
	mu        sync.Mutex
	tokens    []string
	idx       int
	refreshes int
	// refuse makes Refresh fail, standing in for a credential that cannot be
	// renewed.
	refuse bool
}

func newCredentials(tokens ...string) *seqCredentials {
	return &seqCredentials{tokens: tokens}
}

func (c *seqCredentials) Token(context.Context) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.tokens[c.idx], nil
}

func (c *seqCredentials) Refresh(_ context.Context, expired string) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.refreshes++
	if c.refuse {
		return "", fmt.Errorf("the identity provider refused to issue a new token")
	}
	if c.tokens[c.idx] != expired {
		return c.tokens[c.idx], nil
	}
	if c.idx+1 >= len(c.tokens) {
		return "", fmt.Errorf("no further access token is available")
	}
	c.idx++
	return c.tokens[c.idx], nil
}

func (c *seqCredentials) refreshCount() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.refreshes
}

// startMock brings up a mock pinned to the derived contract and stops it when
// the test ends.
func startMock(t *testing.T, cfg mocklcm.Config) *mocklcm.Mock {
	t.Helper()
	if cfg.ContractPath == "" {
		cfg.ContractPath = contractPath
	}
	if cfg.Inventory == nil {
		cfg.Inventory = mocklcm.DefaultInventory()
	}
	if cfg.Depot == nil {
		cfg.Depot = mocklcm.DefaultDepot()
	}
	if cfg.TaskID == "" {
		cfg.TaskID = taskID
	}
	m, err := mocklcm.New(cfg)
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(func() { _ = m.Close() })
	return m
}

// writePlan writes a plan to a temporary file and returns its path.
func writePlan(t *testing.T, plan map[string]any) string {
	t.Helper()
	raw, err := json.MarshalIndent(plan, "", "  ")
	if err != nil {
		t.Fatalf("encode plan: %v", err)
	}
	path := filepath.Join(t.TempDir(), "plan.json")
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		t.Fatalf("write plan: %v", err)
	}
	return path
}

// depotSpec is the Fleet depot every test plan points at.
func depotSpec() map[string]any {
	return map[string]any{
		"fqdn":        "depot.vcf.example.com",
		"certificate": "-----BEGIN CERTIFICATE-----\ndepot\n-----END CERTIFICATE-----\n",
	}
}

// runPlan carries out a plan against a mock.
func runPlan(t *testing.T, m *mocklcm.Mock, plan string, creds fleetrun.CredentialSource) (*fleetrun.Report, error) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	return fleetrun.Run(ctx, fleetrun.Options{
		PlanPath:     plan,
		ContractPath: contractPath,
		BaseURL:      m.URL(),
		Credentials:  creds,
		PollInterval: pollInterval,
		PollTimeout:  20 * time.Second,
	})
}

// requireNoViolations fails the test when the mock rejected anything the run
// sent.
func requireNoViolations(t *testing.T, m *mocklcm.Mock) {
	t.Helper()
	for _, v := range m.Violations() {
		t.Errorf("the service rejected a request: %s", v)
	}
}

// operations lists the operationIds of the requests the mock routed, in order.
func operations(m *mocklcm.Mock) []string {
	var out []string
	for _, r := range m.Requests() {
		out = append(out, r.OperationID)
	}
	return out
}

// statuses lists the status each request was answered with, in order.
func statuses(m *mocklcm.Mock) []int {
	var out []int
	for _, r := range m.Requests() {
		out = append(out, r.Status)
	}
	return out
}

// requestsFor returns the requests routed to one operation.
func requestsFor(m *mocklcm.Mock, operationID string) []mocklcm.Record {
	var out []mocklcm.Record
	for _, r := range m.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// only returns the single request routed to an operation, failing when there is
// not exactly one.
func only(t *testing.T, m *mocklcm.Mock, operationID string) mocklcm.Record {
	t.Helper()
	got := requestsFor(m, operationID)
	if len(got) != 1 {
		t.Fatalf("want exactly one %s request, got %d", operationID, len(got))
	}
	return got[0]
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// pretty renders a value for a failure message.
func pretty(v any) string {
	raw, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return fmt.Sprintf("%#v", v)
	}
	return string(raw)
}
