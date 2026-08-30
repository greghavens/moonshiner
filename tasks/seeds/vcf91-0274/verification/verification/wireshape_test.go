// Package verification holds the protected acceptance suite for opsdiag.
//
// It drives the package against the contract-pinned loopback mock in
// internal/opsmock and asserts both the diagnosis and the exact wire shape of
// every request the package puts on the network. No VMware endpoint is
// contacted.
package verification

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"

	"vcfops/internal/opsmock"
	"vcfops/opsdiag"
)

const resourceID = "9d3a1f52-77b0-4c1e-9a44-0f9b2c7e51ad"

func newClient(t *testing.T, contractPath, fixtureSet string) (*opsdiag.Client, *opsmock.Server) {
	t.Helper()
	srv, err := opsmock.New(contractPath, fixtureSet)
	if err != nil {
		t.Fatalf("opsmock.New(%q, %q): %v", contractPath, fixtureSet, err)
	}
	t.Cleanup(srv.Close)

	contract, err := opsdiag.LoadContract(contractPath)
	if err != nil {
		t.Fatalf("LoadContract(%q): %v", contractPath, err)
	}
	client, err := opsdiag.NewClient(srv.URL(), opsmock.Token, contract, nil)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if client == nil {
		t.Fatal("NewClient returned a nil client and a nil error")
	}
	return client, srv
}

func TestDiagnoseCorrelatesRetrievedRecords(t *testing.T) {
	t.Parallel()
	contractPath := opsmock.ContractPath()

	cases := []struct {
		name    string
		fixture string
		want    opsdiag.Diagnosis
	}{
		{
			// The loudest contributing symptom is the CRITICAL collection lag,
			// but the IMMEDIATE credential failure started five minutes earlier.
			// An older SystemCollectorDown symptom is present but cancelled and
			// not contributing, so it must not win.
			name:    "earliest contributing symptom wins over the loudest",
			fixture: "adapter-credential-rejected",
			want: opsdiag.Diagnosis{
				ResourceID:              resourceID,
				AlertID:                 "3c9e0a41-2b77-4a83-9c14-6d0f5b21e7aa",
				RootSymptomID:           "e5d2a8f4-1c06-4e39-8b52-77af0d31c9e4",
				RootSymptomDefinitionID: "SystemAdapterInstanceCredentialFailure",
				RootCause:               opsdiag.RootCauseCredentialRejected,
				ObjectsConfigured:       1204,
				ObjectsCollecting:       1002,
				ObjectsNotCollecting:    202,
				Notes: []string{
					"Paged infra on-call. No vCenter maintenance window was open.",
					"IAM automation rotated the svc-vcops-collector service account password at 2026-05-02T02:14Z; the VCF Operations adapter credential was not updated.",
				},
			},
		},
		{
			name:    "collector outage is the earliest contributing condition",
			fixture: "collector-offline",
			want: opsdiag.Diagnosis{
				ResourceID:              resourceID,
				AlertID:                 "b8e14f27-9c03-4d6a-a1f5-2e70bd394c88",
				RootSymptomID:           "9a2467bd-05fc-4318-92e1-c840a7f36b52",
				RootSymptomDefinitionID: "SystemCollectorDown",
				RootCause:               opsdiag.RootCauseCollectorOffline,
				ObjectsConfigured:       1204,
				ObjectsCollecting:       0,
				ObjectsNotCollecting:    1204,
				Notes: []string{
					"Cloud proxy cp-prod-b lost management network connectivity during the ToR upgrade.",
				},
			},
		},
		{
			// Two CRITICAL alerts: the earlier start time breaks the tie.
			name:    "equal criticality is broken by the earlier alert start",
			fixture: "monitoring-stopped",
			want: opsdiag.Diagnosis{
				ResourceID:              resourceID,
				AlertID:                 "5f8b2c14-90d7-4a63-b1e8-0c6749a3f582",
				RootSymptomID:           "2e6a48fb-c751-4d09-93b7-8a0f5e14d6c3",
				RootSymptomDefinitionID: "SystemAdapterInstanceMonitoringStopped",
				RootCause:               opsdiag.RootCauseMonitoringStopped,
				ObjectsConfigured:       1204,
				ObjectsCollecting:       1004,
				ObjectsNotCollecting:    200,
				Notes: []string{
					"Monitoring was stopped for the storage migration and never restarted.",
				},
			},
		},
		{
			// A contributing symptom names SystemAdapterInstanceCredentialFailure
			// but has no symptom record, so it cannot be timed and cannot be the
			// root. The only timeable contributing symptom maps to nothing.
			name:    "untimeable contributing symptom is not a root cause",
			fixture: "unclassified-root-symptom",
			want: opsdiag.Diagnosis{
				ResourceID:              resourceID,
				AlertID:                 "7c3d9b60-1e48-4fa2-85d3-b9026ce41f7a",
				RootSymptomID:           "31c8e57a-2f94-4d61-a708-6b53d0f19e42",
				RootSymptomDefinitionID: "SystemHostCpuContention",
				RootCause:               opsdiag.RootCauseUnclassified,
				ObjectsConfigured:       1204,
				ObjectsCollecting:       1198,
				ObjectsNotCollecting:    6,
				Notes: []string{
					"Contention started right after the DRS cluster was placed in manual mode.",
				},
			},
		},
		{
			name:    "no alerts leaves the alert scoped fields empty",
			fixture: "no-active-alerts",
			want: opsdiag.Diagnosis{
				ResourceID:           resourceID,
				RootCause:            opsdiag.RootCauseNoActiveAlerts,
				ObjectsConfigured:    1204,
				ObjectsCollecting:    1204,
				ObjectsNotCollecting: 0,
			},
		},
		{
			// The alert data exercises the complete lower half of the alert
			// criticality order plus both deterministic alert tie-breakers. The
			// symptoms share their earliest timestamp, so criticality and then id
			// must decide the root.
			name:    "criticality and id break otherwise equal ties",
			fixture: "deterministic-ties",
			want: opsdiag.Diagnosis{
				ResourceID:              resourceID,
				AlertID:                 "a0000000-0000-4000-8000-000000000001",
				RootSymptomID:           "a0000000-0000-4000-8000-000000000011",
				RootSymptomDefinitionID: "SystemCollectorDown",
				RootCause:               opsdiag.RootCauseCollectorOffline,
				ObjectsConfigured:       1204,
				ObjectsCollecting:       1100,
				ObjectsNotCollecting:    104,
				Notes: []string{
					"Deterministic tie-break fixture selected the expected alert.",
				},
			},
		},
		{
			name:    "no timeable contributing symptom leaves root fields empty",
			fixture: "no-timeable-root",
			want: opsdiag.Diagnosis{
				ResourceID:           resourceID,
				AlertID:              "90000000-0000-4000-8000-000000000001",
				RootCause:            opsdiag.RootCauseUnclassified,
				ObjectsConfigured:    1204,
				ObjectsCollecting:    1190,
				ObjectsNotCollecting: 14,
				Notes: []string{
					"The contributing symptom has no matching symptom record.",
				},
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			client, srv := newClient(t, contractPath, tc.fixture)

			got, err := client.Diagnose(context.Background(), resourceID)
			if err != nil {
				t.Fatalf("Diagnose: %v", err)
			}
			if got == nil {
				t.Fatal("Diagnose returned a nil diagnosis and a nil error")
			}
			if len(got.Notes) == 0 && len(tc.want.Notes) == 0 {
				got.Notes, tc.want.Notes = nil, nil
			}
			if !reflect.DeepEqual(*got, tc.want) {
				t.Errorf("diagnosis mismatch\n got: %+v\nwant: %+v", *got, tc.want)
			}
			for _, r := range srv.Requests() {
				if r.OperationID == "" {
					t.Errorf("request %s %s matches no operation in the contract", r.Method, r.Path)
				}
				if r.Status != http.StatusOK {
					t.Errorf("%s %s?%s answered %d, want 200", r.Method, r.Path, r.RawQuery, r.Status)
				}
			}
		})
	}
}

func TestRequestWireShape(t *testing.T) {
	t.Parallel()
	client, srv := newClient(t, opsmock.ContractPath(), "adapter-credential-rejected")

	if _, err := client.Diagnose(context.Background(), resourceID); err != nil {
		t.Fatalf("Diagnose: %v", err)
	}

	wantOrder := []string{
		"queryAlert",
		"getAlertContributingSymptoms",
		"getAlertNotes",
		"querySymptoms",
		"getSystemAudit",
	}
	if got := srv.OperationIDs(); !reflect.DeepEqual(got, wantOrder) {
		t.Fatalf("operation sequence\n got: %v\nwant: %v", got, wantOrder)
	}
	reqs := srv.Requests()

	// -------------------------------------------------- queryAlert
	q := reqs[0]
	assertLine(t, q, http.MethodPost, "/suite-api/api/alerts/query")
	assertHeaders(t, q, true)
	assertQuery(t, q, map[string][]string{"page": {"0"}, "pageSize": {"100"}})
	assertBody(t, q, map[string]any{
		"resource-query":    map[string]any{"resourceId": []any{resourceID}},
		"activeOnly":        true,
		"alertCriticality":  []any{"CRITICAL", "IMMEDIATE"},
		"compositeOperator": "AND",
	})

	// -------------------------------------------------- getAlertContributingSymptoms
	//
	// Both alert ids, in the order the alert query returned them, as repeated
	// keys: style=form, explode=true. A comma joined single value is wrong.
	cs := reqs[1]
	assertLine(t, cs, http.MethodGet, "/suite-api/api/alerts/contributingsymptoms")
	assertHeaders(t, cs, false)
	assertNoBody(t, cs)
	assertQuery(t, cs, map[string][]string{"id": {
		"0f2c6d18-3a95-4b70-8e21-5c4d9a7f0b13",
		"3c9e0a41-2b77-4a83-9c14-6d0f5b21e7aa",
	}})
	if n := strings.Count(cs.RawQuery, "id="); n != 2 {
		t.Errorf("contributing symptoms raw query %q carries %d id= keys, want 2", cs.RawQuery, n)
	}

	// -------------------------------------------------- getAlertNotes
	//
	// page and pageSize are optional here and are not set, so they must not be
	// sent at all.
	n := reqs[2]
	assertLine(t, n, http.MethodGet, "/suite-api/api/alerts/3c9e0a41-2b77-4a83-9c14-6d0f5b21e7aa/notes")
	assertHeaders(t, n, false)
	assertNoBody(t, n)
	assertQuery(t, n, map[string][]string{})

	// -------------------------------------------------- querySymptoms
	//
	// activeOnly is deliberately false and must appear on the wire as false;
	// dropping it would silently change the query to its active-only default.
	s := reqs[3]
	assertLine(t, s, http.MethodPost, "/suite-api/api/symptoms/query")
	assertHeaders(t, s, true)
	assertQuery(t, s, map[string][]string{"page": {"0"}, "pageSize": {"100"}})
	assertBody(t, s, map[string]any{
		"resource-query":    map[string]any{"resourceId": []any{resourceID}},
		"activeOnly":        false,
		"includeAlarmInfo":  true,
		"compositeOperator": "AND",
	})

	// -------------------------------------------------- getSystemAudit
	a := reqs[4]
	assertLine(t, a, http.MethodGet, "/suite-api/api/audit/system")
	assertHeaders(t, a, false)
	assertNoBody(t, a)
	assertQuery(t, a, map[string][]string{})
}

func TestNoAlertsSkipsAlertScopedOperations(t *testing.T) {
	t.Parallel()
	client, srv := newClient(t, opsmock.ContractPath(), "no-active-alerts")

	if _, err := client.Diagnose(context.Background(), resourceID); err != nil {
		t.Fatalf("Diagnose: %v", err)
	}
	want := []string{"queryAlert", "querySymptoms", "getSystemAudit"}
	if got := srv.OperationIDs(); !reflect.DeepEqual(got, want) {
		t.Fatalf("operation sequence\n got: %v\nwant: %v", got, want)
	}
}

// TestRequestMetadataComesFromTheContract rewrites the base path, every
// operation path, one method, and the authorization header and scheme. A
// client that resolves all of those values from the contract follows the mock;
// one that hardcodes any of them is routed nowhere or rejected.
func TestRequestMetadataComesFromTheContract(t *testing.T) {
	t.Parallel()

	raw, err := os.ReadFile(opsmock.ContractPath())
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse contract: %v", err)
	}
	doc["basePath"] = "/suite-api-relocated"
	auth, ok := doc["authorization"].(map[string]any)
	if !ok {
		t.Fatal("contract authorization is not an object")
	}
	auth["header"] = "X-VCF-Test-Token"
	auth["scheme"] = "ContractScheme"
	ops, ok := doc["operations"].(map[string]any)
	if !ok {
		t.Fatal("contract operations is not an object")
	}
	for name, rawOp := range ops {
		op, ok := rawOp.(map[string]any)
		if !ok {
			t.Fatalf("operation %s is not an object", name)
		}
		path, ok := op["path"].(string)
		if !ok {
			t.Fatalf("operation %s has no string path", name)
		}
		op["path"] = "/contract-paths" + path
	}
	auditOp := ops["getSystemAudit"].(map[string]any)
	auditOp["method"] = http.MethodPost
	out, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		t.Fatalf("marshal contract: %v", err)
	}
	variant := filepath.Join(t.TempDir(), "contract.json")
	if err := os.WriteFile(variant, out, 0o644); err != nil {
		t.Fatalf("write contract: %v", err)
	}

	client, srv := newClient(t, variant, "collector-offline")
	got, err := client.Diagnose(context.Background(), resourceID)
	if err != nil {
		t.Fatalf("Diagnose against relocated base path: %v", err)
	}
	if got.RootCause != opsdiag.RootCauseCollectorOffline {
		t.Errorf("RootCause = %q, want %q", got.RootCause, opsdiag.RootCauseCollectorOffline)
	}
	reqs := srv.Requests()
	for _, r := range reqs {
		if !strings.HasPrefix(r.Path, "/suite-api-relocated/contract-paths/") {
			t.Errorf("request path %q ignores the contract base path or operation path", r.Path)
		}
		if got := r.Header.Get("X-VCF-Test-Token"); got != "ContractScheme "+opsmock.Token {
			t.Errorf("%s: contract authorization header = %q", r.OperationID, got)
		}
		if got := r.Header.Get("Authorization"); got != "" {
			t.Errorf("%s: hardcoded Authorization header = %q", r.OperationID, got)
		}
	}
	if got := reqs[len(reqs)-1].Method; got != http.MethodPost {
		t.Errorf("getSystemAudit method = %q, want contract method POST", got)
	}
}

func TestLoadContractRejectsMissingRequiredMetadata(t *testing.T) {
	t.Parallel()
	raw, err := os.ReadFile(opsmock.ContractPath())
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}

	cases := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{
			name: "base path",
			mutate: func(doc map[string]any) {
				delete(doc, "basePath")
			},
		},
		{
			name: "authorization header",
			mutate: func(doc map[string]any) {
				delete(doc["authorization"].(map[string]any), "header")
			},
		},
		{
			name: "authorization scheme",
			mutate: func(doc map[string]any) {
				delete(doc["authorization"].(map[string]any), "scheme")
			},
		},
		{
			name: "operations",
			mutate: func(doc map[string]any) {
				doc["operations"] = map[string]any{}
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			var doc map[string]any
			if err := json.Unmarshal(raw, &doc); err != nil {
				t.Fatalf("parse contract: %v", err)
			}
			tc.mutate(doc)
			out, err := json.Marshal(doc)
			if err != nil {
				t.Fatalf("marshal contract: %v", err)
			}
			path := filepath.Join(t.TempDir(), "contract.json")
			if err := os.WriteFile(path, out, 0o644); err != nil {
				t.Fatalf("write contract: %v", err)
			}
			if _, err := opsdiag.LoadContract(path); err == nil {
				t.Fatalf("LoadContract accepted a contract with no %s", tc.name)
			}
		})
	}
}

func TestNewClientRejectsIncompleteContract(t *testing.T) {
	t.Parallel()
	if _, err := opsdiag.NewClient("http://127.0.0.1:1", "token", nil, nil); !errors.Is(err, opsdiag.ErrIncompleteContract) {
		t.Fatalf("NewClient(nil contract) error = %v, want ErrIncompleteContract", err)
	}

	raw, err := os.ReadFile(opsmock.ContractPath())
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse contract: %v", err)
	}
	ops, _ := doc["operations"].(map[string]any)
	if len(ops) == 0 {
		t.Fatal("contract names no operations")
	}
	names := make([]string, 0, len(ops))
	for name := range ops {
		names = append(names, name)
	}
	sort.Strings(names)

	for _, drop := range names {
		t.Run("without_"+drop, func(t *testing.T) {
			t.Parallel()
			trimmed := make(map[string]any, len(ops))
			for name, op := range ops {
				if name != drop {
					trimmed[name] = op
				}
			}
			variant := map[string]any{}
			for k, v := range doc {
				variant[k] = v
			}
			variant["operations"] = trimmed

			out, err := json.MarshalIndent(variant, "", "  ")
			if err != nil {
				t.Fatalf("marshal contract: %v", err)
			}
			path := filepath.Join(t.TempDir(), "contract.json")
			if err := os.WriteFile(path, out, 0o644); err != nil {
				t.Fatalf("write contract: %v", err)
			}

			contract, err := opsdiag.LoadContract(path)
			if err != nil {
				t.Fatalf("LoadContract rejected a contract that still names operations: %v", err)
			}
			if _, err := opsdiag.NewClient("http://127.0.0.1:1", "token", contract, nil); err == nil {
				t.Fatalf("NewClient accepted a contract missing %q", drop)
			} else if !errors.Is(err, opsdiag.ErrIncompleteContract) {
				t.Fatalf("NewClient error = %v, want ErrIncompleteContract", err)
			}
		})
	}
}

func TestEveryNonOKResponseAbortsDiagnosis(t *testing.T) {
	t.Parallel()
	operations := []string{
		"queryAlert",
		"getAlertContributingSymptoms",
		"getAlertNotes",
		"querySymptoms",
		"getSystemAudit",
	}
	for _, operationID := range operations {
		t.Run(operationID, func(t *testing.T) {
			t.Parallel()
			client, srv := newClient(t, opsmock.ContractPath(), "adapter-credential-rejected")
			// A valid fixture body with a 201 catches clients that accept every
			// 2xx response even though this contract requires exactly 200.
			srv.ForceStatus(operationID, http.StatusCreated)

			got, err := client.Diagnose(context.Background(), resourceID)
			if err == nil {
				t.Fatalf("Diagnose returned no error after %s returned 201", operationID)
			}
			if got != nil {
				t.Fatalf("Diagnose returned a partial result after %s returned 201: %+v", operationID, got)
			}
			reqs := srv.Requests()
			if len(reqs) == 0 || reqs[len(reqs)-1].OperationID != operationID {
				t.Fatalf("last operation = %v, want failed operation %s", srv.OperationIDs(), operationID)
			}
			if reqs[len(reqs)-1].Status != http.StatusCreated {
				t.Fatalf("%s status = %d, want 201", operationID, reqs[len(reqs)-1].Status)
			}
		})
	}
}

func TestConcurrentDiagnoseIsRaceFree(t *testing.T) {
	t.Parallel()
	client, _ := newClient(t, opsmock.ContractPath(), "adapter-credential-rejected")

	const goroutines = 8
	var wg sync.WaitGroup
	results := make([]*opsdiag.Diagnosis, goroutines)
	errs := make([]error, goroutines)
	for i := range goroutines {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i], errs[i] = client.Diagnose(context.Background(), resourceID)
		}()
	}
	wg.Wait()

	for i := range goroutines {
		if errs[i] != nil {
			t.Fatalf("goroutine %d: %v", i, errs[i])
		}
		if !reflect.DeepEqual(results[i], results[0]) {
			t.Errorf("goroutine %d produced %+v, want %+v", i, results[i], results[0])
		}
	}
}

// ---------------------------------------------------------------- helpers

func assertLine(t *testing.T, r opsmock.Request, method, path string) {
	t.Helper()
	if r.Method != method {
		t.Errorf("%s: method = %s, want %s", r.OperationID, r.Method, method)
	}
	if r.Path != path {
		t.Errorf("%s: path = %s, want %s", r.OperationID, r.Path, path)
	}
}

func assertHeaders(t *testing.T, r opsmock.Request, wantJSONBody bool) {
	t.Helper()
	const wantAuth = "vRealizeOpsToken " + opsmock.Token
	if got := r.Header.Get("Authorization"); got != wantAuth {
		t.Errorf("%s: Authorization = %q, want %q", r.OperationID, got, wantAuth)
	}
	if got := r.Header.Get("Accept"); got != "application/json" {
		t.Errorf("%s: Accept = %q, want application/json", r.OperationID, got)
	}
	got := r.Header.Get("Content-Type")
	switch {
	case wantJSONBody && got != "application/json":
		t.Errorf("%s: Content-Type = %q, want application/json", r.OperationID, got)
	case !wantJSONBody && got != "":
		t.Errorf("%s: bodyless request set Content-Type = %q", r.OperationID, got)
	}
}

func assertQuery(t *testing.T, r opsmock.Request, want map[string][]string) {
	t.Helper()
	got := map[string][]string(r.Query)
	if len(got) == 0 && len(want) == 0 {
		return
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s: query = %v, want %v (raw %q)", r.OperationID, got, want, r.RawQuery)
	}
}

func assertNoBody(t *testing.T, r opsmock.Request) {
	t.Helper()
	if len(r.Body) != 0 {
		t.Errorf("%s: sent a %d byte body on a %s, want none: %s",
			r.OperationID, len(r.Body), r.Method, r.Body)
	}
}

// assertBody requires the decoded request body to equal want exactly. Any key
// the caller did not ask for is a failure, which is what makes this an
// assertion that unset optional fields are omitted rather than sent empty.
func assertBody(t *testing.T, r opsmock.Request, want map[string]any) {
	t.Helper()
	if len(r.Body) == 0 {
		t.Fatalf("%s: sent no request body", r.OperationID)
	}
	var got map[string]any
	dec := json.NewDecoder(strings.NewReader(string(r.Body)))
	if err := dec.Decode(&got); err != nil {
		t.Fatalf("%s: body is not a JSON object: %v (%s)", r.OperationID, err, r.Body)
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s: request body mismatch\n got: %s\nwant: %s",
			r.OperationID, mustJSON(t, got), mustJSON(t, want))
	}
}

func mustJSON(t *testing.T, v any) []byte {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	return b
}
