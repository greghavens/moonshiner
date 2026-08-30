// Package contracttest drives the restore drill against a loopback mock of the
// SDDC LCM service whose routes are built from docs/contract.json, and asserts
// the exact shape of every request the drill put on the wire.
//
// This package is a fixed input. Do not edit it.
package contracttest

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"example.com/vcf/restoredrill/internal/mocklcm"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"
	token        = "eyJhbGciOiJIUzI1NiJ9.drill"

	vidbID   = mocklcm.SampleVidbID
	opscpID  = mocklcm.SampleOpscpID
	vcfopsID = mocklcm.SampleVcfopsID
	vcfaID   = mocklcm.SampleVcfaID
)

// startMock brings up the mock with the shared fixtures and the given tweaks.
func startMock(t *testing.T, opts mocklcm.Options) *mocklcm.Server {
	t.Helper()
	if opts.ContractPath == "" {
		opts.ContractPath = contractPath
	}
	opts.Token = token
	if opts.Components == nil {
		opts.Components = mocklcm.SampleComponents()
	}
	if opts.Backups == nil {
		opts.Backups = mocklcm.SampleBackups()
	}
	server, err := mocklcm.Start(opts)
	if err != nil {
		t.Fatalf("the mock could not be built from %s: %v", opts.ContractPath, err)
	}
	t.Cleanup(server.Close)
	return server
}

// mockWithContract configures a mock built from a contract other than the one
// the drill package ships with.
func mockWithContract(path string) mocklcm.Options {
	return mocklcm.Options{ContractPath: path, PollsBeforeTerminal: 3}
}

// s0 returns the first request that matched the named operation.
func s0(t *testing.T, server *mocklcm.Server, operationID string) mocklcm.Recorded {
	t.Helper()
	got := server.Requests(operationID)
	if len(got) == 0 {
		t.Fatalf("the drill never called %s", operationID)
	}
	return got[0]
}

// writePlan puts a plan on disk and returns its path.
func writePlan(t *testing.T, plan map[string]any) string {
	t.Helper()
	raw, err := json.MarshalIndent(plan, "", "  ")
	if err != nil {
		t.Fatalf("encode plan: %v", err)
	}
	path := filepath.Join(t.TempDir(), "restore-plan.json")
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		t.Fatalf("write plan: %v", err)
	}
	return path
}

// asJSON round-trips a value through JSON so it can be compared structurally.
func asJSON(t *testing.T, v any) any {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	return out
}

// parseJSON decodes a literal used as an expectation.
func parseJSON(t *testing.T, text string) any {
	t.Helper()
	var out any
	if err := json.Unmarshal([]byte(text), &out); err != nil {
		t.Fatalf("expectation is not valid JSON: %v", err)
	}
	return out
}

func requireEqualJSON(t *testing.T, what string, got, want any) {
	t.Helper()
	if reflect.DeepEqual(got, want) {
		return
	}
	gotText, _ := json.MarshalIndent(got, "", "  ")
	wantText, _ := json.MarshalIndent(want, "", "  ")
	t.Errorf("%s does not match.\n got: %s\nwant: %s", what, gotText, wantText)
}

func requireNoViolations(t *testing.T, server *mocklcm.Server) {
	t.Helper()
	for _, v := range server.Violations() {
		t.Errorf("the drill broke the contract: %s", v)
	}
}

// requireQuery asserts the exact set of query parameters on one request.
func requireQuery(t *testing.T, rec mocklcm.Recorded, want map[string]string) {
	t.Helper()
	got := map[string]string{}
	for name, values := range rec.Query {
		if len(values) != 1 {
			t.Errorf("%s: query parameter %q was sent %d times", rec.OperationID, name, len(values))
			continue
		}
		got[name] = values[0]
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s (request %d) query is %v, want %v", rec.OperationID, rec.Seq, got, want)
	}
	if len(want) == 0 && rec.RawQuery != "" {
		t.Errorf("%s (request %d) sent a query string %q; with no values to send there is no query string",
			rec.OperationID, rec.Seq, rec.RawQuery)
	}
}

// requireBody asserts the exact JSON body of one request.
func requireBody(t *testing.T, rec mocklcm.Recorded, want string) {
	t.Helper()
	got, err := rec.BodyJSON()
	if err != nil {
		t.Fatalf("%v", err)
	}
	requireEqualJSON(t, rec.OperationID+" request body", got, parseJSON(t, want))
}

// requireAuthorized asserts every request carried the credential the contract's
// security scheme calls for.
func requireAuthorized(t *testing.T, server *mocklcm.Server) {
	t.Helper()
	want := server.AuthorizationHeader()
	for _, rec := range server.Log() {
		if got := rec.Header.Get("Authorization"); got != want {
			t.Errorf("request %d (%s) Authorization is %q, want %q", rec.Seq, rec.OperationID, got, want)
		}
	}
}

// requireNoHeaderAnywhere asserts a header was never sent.
func requireNoHeaderAnywhere(t *testing.T, server *mocklcm.Server, name string) {
	t.Helper()
	for _, rec := range server.Log() {
		if got := rec.Header.Get(name); got != "" {
			t.Errorf("request %d (%s) sent %s: %q; with no value to send the header is omitted",
				rec.Seq, rec.OperationID, name, got)
		}
	}
}
