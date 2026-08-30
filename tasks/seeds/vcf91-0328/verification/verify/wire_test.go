package verify

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"

	"example.com/vcfauto"
	"example.com/vcfauto/mockapi"
)

const (
	testToken = "s3cr3t-bearer-token"
	depID     = "e0f5a2c4-9b13-4f77-8a2e-3c1d6b0e9f21"

	actPowerOff = "Deployment.PowerOff"
	actResize   = "Deployment.Resize"
	actDelete   = "Deployment.Delete"
)

func seed(actions ...mockapi.Action) map[string]mockapi.Deployment {
	return map[string]mockapi.Deployment{depID: {Actions: actions}}
}

func action(id string, valid bool) mockapi.Action {
	return mockapi.Action{
		ID:          id,
		Name:        id,
		DisplayName: id,
		ActionType:  "RESOURCE_ACTION",
		Valid:       valid,
	}
}

func startMock(t *testing.T, opts mockapi.Options) *mockapi.Server {
	t.Helper()
	if opts.ContractPath == "" {
		opts.ContractPath = contractPath
	}
	if opts.Token == "" {
		opts.Token = testToken
	}
	s, err := mockapi.Start(opts)
	if err != nil {
		t.Fatalf("mockapi.Start: %v", err)
	}
	t.Cleanup(s.Close)
	return s
}

func newClient(t *testing.T, baseURL string) *vcfauto.Client {
	t.Helper()
	c, err := vcfauto.New(vcfauto.Config{BaseURL: baseURL, Token: testToken})
	if err != nil {
		t.Fatalf("vcfauto.New: %v", err)
	}
	if c == nil {
		t.Fatal("vcfauto.New returned a nil Client and a nil error")
	}
	return c
}

func methods(recs []mockapi.Recorded) []string {
	out := make([]string, 0, len(recs))
	for _, r := range recs {
		out = append(out, r.Method+" "+r.Path)
	}
	return out
}

func countMutating(recs []mockapi.Recorded) int {
	n := 0
	for _, r := range recs {
		if r.Method == http.MethodPost {
			n++
		}
	}
	return n
}

func bodyKeys(t *testing.T, b []byte) []string {
	t.Helper()
	var m map[string]json.RawMessage
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("request body is not a JSON object: %v (body %q)", err, string(b))
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// ---------------------------------------------------------------------------
// The gate
// ---------------------------------------------------------------------------

// TestPrecheckGatesTheMutatingCall is the point of the exercise: when the
// precheck says no, nothing is changed.
func TestPrecheckGatesTheMutatingCall(t *testing.T) {
	tests := []struct {
		name        string
		actions     []mockapi.Action
		wantErr     error
		wantMutated bool
	}{
		{
			name:        "action available and valid",
			actions:     []mockapi.Action{action(actPowerOff, true), action(actResize, false)},
			wantMutated: true,
		},
		{
			name:        "a valid matching entry passes even after an invalid duplicate",
			actions:     []mockapi.Action{action(actPowerOff, false), action(actPowerOff, true)},
			wantMutated: true,
		},
		{
			name:    "action not listed for the deployment",
			actions: []mockapi.Action{action(actResize, true), action(actDelete, true)},
			wantErr: vcfauto.ErrActionNotFound,
		},
		{
			name:    "action listed but invalid for current state",
			actions: []mockapi.Action{action(actPowerOff, false), action(actResize, true)},
			wantErr: vcfauto.ErrActionNotValid,
		},
		{
			name:    "deployment has no available actions",
			actions: nil,
			wantErr: vcfauto.ErrActionNotFound,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, mockapi.Options{Deployments: seed(tc.actions...)})
			c := newClient(t, srv.URL())

			got, err := c.SubmitAction(context.Background(), vcfauto.ActionRequest{
				DeploymentID: depID,
				ActionID:     actPowerOff,
			})

			recs := srv.Requests()
			if tc.wantErr != nil {
				if !errors.Is(err, tc.wantErr) {
					t.Fatalf("SubmitAction error = %v, want errors.Is(err, %v)", err, tc.wantErr)
				}
				if got != nil {
					t.Errorf("SubmitAction returned %+v alongside the gate error, want nil", got)
				}
				if n := countMutating(recs); n != 0 {
					t.Errorf("the gate failed but %d mutating request(s) were sent: %v", n, methods(recs))
				}
				if len(recs) != 1 {
					t.Errorf("recorded %d requests %v, want exactly the precheck read", len(recs), methods(recs))
				}
				return
			}

			if err != nil {
				t.Fatalf("SubmitAction: %v", err)
			}
			if got == nil {
				t.Fatal("SubmitAction returned a nil Request and a nil error")
			}
			if got.ActionID != actPowerOff {
				t.Errorf("Request.ActionID = %q, want %q", got.ActionID, actPowerOff)
			}
			if got.DeploymentID != depID {
				t.Errorf("Request.DeploymentID = %q, want %q", got.DeploymentID, depID)
			}
			if got.ID == "" {
				t.Error("Request.ID is empty")
			}
			if got.Status == "" {
				t.Error("Request.Status is empty")
			}
			if len(recs) != 2 {
				t.Fatalf("recorded %v, want the precheck read then the mutating call", methods(recs))
			}
			if recs[0].Method != http.MethodGet || recs[1].Method != http.MethodPost {
				t.Errorf("request order = %v, want the precheck read first", methods(recs))
			}
		})
	}
}

// TestMalformedRequestNeverReachesTheNetwork covers the arguments that are
// rejected before any HTTP request is made.
func TestMalformedRequestNeverReachesTheNetwork(t *testing.T) {
	tests := []struct {
		name string
		req  vcfauto.ActionRequest
	}{
		{"empty deployment id", vcfauto.ActionRequest{ActionID: actPowerOff}},
		{"empty action id", vcfauto.ActionRequest{DeploymentID: depID}},
		{"deployment id with a path separator", vcfauto.ActionRequest{DeploymentID: "a/b", ActionID: actPowerOff}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, mockapi.Options{Deployments: seed(action(actPowerOff, true))})
			c := newClient(t, srv.URL())

			_, err := c.SubmitAction(context.Background(), tc.req)
			if !errors.Is(err, vcfauto.ErrInvalidRequest) {
				t.Fatalf("SubmitAction error = %v, want errors.Is(err, ErrInvalidRequest)", err)
			}
			if recs := srv.Requests(); len(recs) != 0 {
				t.Errorf("recorded %v, want no request at all", methods(recs))
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Wire shape
// ---------------------------------------------------------------------------

func TestPrecheckRequestWireShape(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: seed(action(actPowerOff, true))})
	c := newClient(t, srv.URL())

	if _, err := c.ListActions(context.Background(), depID); err != nil {
		t.Fatalf("ListActions: %v", err)
	}

	recs := srv.Requests()
	if len(recs) != 1 {
		t.Fatalf("recorded %v, want one request", methods(recs))
	}
	r := recs[0]

	if r.Method != http.MethodGet {
		t.Errorf("method = %q, want GET", r.Method)
	}
	if want := "/deployment/api/deployments/" + depID + "/actions"; r.Path != want {
		t.Errorf("path = %q, want %q", r.Path, want)
	}
	if r.RawQuery != "" {
		t.Errorf("query = %q, want none: the operation documents no query parameters", r.RawQuery)
	}
	if len(r.Body) != 0 {
		t.Errorf("body = %q, want empty", string(r.Body))
	}
	if want := "Bearer " + testToken; r.Header.Get("Authorization") != want {
		t.Errorf("Authorization = %q, want %q", r.Header.Get("Authorization"), want)
	}
	if got := r.Header.Get("Accept"); got != "application/json" {
		t.Errorf("Accept = %q, want %q", got, "application/json")
	}
}

// TestMutatingRequestOmitsUnsetOptionalFields is the wire-shape assertion the
// contract's omitWhenUnset flags describe: optional fields are absent, not sent
// empty.
func TestMutatingRequestOmitsUnsetOptionalFields(t *testing.T) {
	tests := []struct {
		name     string
		req      vcfauto.ActionRequest
		wantKeys []string
		wantBody map[string]any
	}{
		{
			name:     "no optional fields set",
			req:      vcfauto.ActionRequest{DeploymentID: depID, ActionID: actPowerOff},
			wantKeys: []string{"actionId"},
			wantBody: map[string]any{"actionId": actPowerOff},
		},
		{
			name:     "reason only",
			req:      vcfauto.ActionRequest{DeploymentID: depID, ActionID: actPowerOff, Reason: "quarterly maintenance"},
			wantKeys: []string{"actionId", "reason"},
			wantBody: map[string]any{"actionId": actPowerOff, "reason": "quarterly maintenance"},
		},
		{
			name:     "inputs only",
			req:      vcfauto.ActionRequest{DeploymentID: depID, ActionID: actPowerOff, Inputs: map[string]any{"cpuCount": float64(4)}},
			wantKeys: []string{"actionId", "inputs"},
			wantBody: map[string]any{"actionId": actPowerOff, "inputs": map[string]any{"cpuCount": float64(4)}},
		},
		{
			name:     "empty inputs map is still omitted",
			req:      vcfauto.ActionRequest{DeploymentID: depID, ActionID: actPowerOff, Inputs: map[string]any{}},
			wantKeys: []string{"actionId"},
			wantBody: map[string]any{"actionId": actPowerOff},
		},
		{
			name: "both optional fields set",
			req: vcfauto.ActionRequest{
				DeploymentID: depID,
				ActionID:     actPowerOff,
				Inputs:       map[string]any{"cpuCount": float64(8), "note": "scale up"},
				Reason:       "capacity",
			},
			wantKeys: []string{"actionId", "inputs", "reason"},
			wantBody: map[string]any{
				"actionId": actPowerOff,
				"inputs":   map[string]any{"cpuCount": float64(8), "note": "scale up"},
				"reason":   "capacity",
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, mockapi.Options{Deployments: seed(action(actPowerOff, true))})
			c := newClient(t, srv.URL())

			if _, err := c.SubmitAction(context.Background(), tc.req); err != nil {
				t.Fatalf("SubmitAction: %v", err)
			}

			recs := srv.Requests()
			if len(recs) != 2 {
				t.Fatalf("recorded %v, want the precheck read then the mutating call", methods(recs))
			}
			r := recs[1]

			if r.Method != http.MethodPost {
				t.Fatalf("method = %q, want POST", r.Method)
			}
			if want := "/deployment/api/deployments/" + depID + "/requests"; r.Path != want {
				t.Errorf("path = %q, want %q", r.Path, want)
			}
			if r.RawQuery != "" {
				t.Errorf("query = %q, want none", r.RawQuery)
			}
			if got := r.Header.Get("Content-Type"); got != "application/json" {
				t.Errorf("Content-Type = %q, want %q", got, "application/json")
			}

			if got := bodyKeys(t, r.Body); !reflect.DeepEqual(got, tc.wantKeys) {
				t.Errorf("body keys = %v, want exactly %v (body %s)", got, tc.wantKeys, r.Body)
			}
			var got map[string]any
			if err := json.Unmarshal(r.Body, &got); err != nil {
				t.Fatalf("decode body: %v", err)
			}
			if !reflect.DeepEqual(got, tc.wantBody) {
				t.Errorf("body = %#v, want %#v", got, tc.wantBody)
			}
		})
	}
}

func TestListActionsDecodesTheValidFlag(t *testing.T) {
	srv := startMock(t, mockapi.Options{
		Deployments: seed(action(actPowerOff, true), action(actResize, false)),
	})
	c := newClient(t, srv.URL())

	got, err := c.ListActions(context.Background(), depID)
	if err != nil {
		t.Fatalf("ListActions: %v", err)
	}
	want := []vcfauto.Action{
		{ID: actPowerOff, Name: actPowerOff, DisplayName: actPowerOff, ActionType: "RESOURCE_ACTION", Valid: true},
		{ID: actResize, Name: actResize, DisplayName: actResize, ActionType: "RESOURCE_ACTION", Valid: false},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ListActions = %+v, want %+v", got, want)
	}
}

// ---------------------------------------------------------------------------
// The mock is pinned to the contract
// ---------------------------------------------------------------------------

// TestMockServesOnlyContractOperations walks paths that exist in the real API
// but are absent from this contract.
func TestMockServesOnlyContractOperations(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: seed(action(actPowerOff, true))})

	tests := []struct {
		name       string
		method     string
		path       string
		wantStatus int
	}{
		{"precheck read", http.MethodGet, "/deployment/api/deployments/" + depID + "/actions", http.StatusOK},
		{"get deployment requests is not in the contract", http.MethodGet, "/deployment/api/deployments/" + depID + "/requests", http.StatusMethodNotAllowed},
		{"post to the precheck path", http.MethodPost, "/deployment/api/deployments/" + depID + "/actions", http.StatusMethodNotAllowed},
		{"get single deployment is not in the contract", http.MethodGet, "/deployment/api/deployments/" + depID, http.StatusNotFound},
		{"get single action is not in the contract", http.MethodGet, "/deployment/api/deployments/" + depID + "/actions/" + actPowerOff, http.StatusNotFound},
		{"get request is not in the contract", http.MethodGet, "/deployment/api/requests/req-1", http.StatusNotFound},
		{"unrelated path", http.MethodGet, "/hello", http.StatusNotFound},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, srv.URL()+tc.path, nil)
			if err != nil {
				t.Fatalf("new request: %v", err)
			}
			req.Header.Set("Authorization", "Bearer "+testToken)
			req.Header.Set("Accept", "application/json")
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("do: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != tc.wantStatus {
				t.Errorf("%s %s = %d, want %d", tc.method, tc.path, resp.StatusCode, tc.wantStatus)
			}
		})
	}
}

// TestMockRoutesFollowTheContractFile drops the mutating operation from a copy
// of the contract; the mock must then stop serving it.
func TestMockRoutesFollowTheContractFile(t *testing.T) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse contract: %v", err)
	}
	ops, ok := doc["operations"].([]any)
	if !ok {
		t.Fatal("contract operations is not an array")
	}
	var kept []any
	for _, o := range ops {
		m, _ := o.(map[string]any)
		if m["id"] == mutatingOpID {
			continue
		}
		kept = append(kept, o)
	}
	if len(kept) != 1 {
		t.Fatalf("kept %d operations, want 1", len(kept))
	}
	doc["operations"] = kept

	trimmed := filepath.Join(t.TempDir(), "contract.json")
	out, err := json.Marshal(doc)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if err := os.WriteFile(trimmed, out, 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	srv := startMock(t, mockapi.Options{
		ContractPath: trimmed,
		Deployments:  seed(action(actPowerOff, true)),
	})

	for _, tc := range []struct {
		name       string
		method     string
		path       string
		wantStatus int
	}{
		{"precheck still served", http.MethodGet, "/deployment/api/deployments/" + depID + "/actions", http.StatusOK},
		{"mutating call no longer routed", http.MethodPost, "/deployment/api/deployments/" + depID + "/requests", http.StatusNotFound},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, srv.URL()+tc.path, nil)
			if err != nil {
				t.Fatalf("new request: %v", err)
			}
			req.Header.Set("Authorization", "Bearer "+testToken)
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("do: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != tc.wantStatus {
				t.Errorf("%s %s = %d, want %d", tc.method, tc.path, resp.StatusCode, tc.wantStatus)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Failures upstream of the mutation
// ---------------------------------------------------------------------------

func TestUpstreamFailuresDoNotMutate(t *testing.T) {
	tests := []struct {
		name       string
		opts       mockapi.Options
		client     func(t *testing.T, url string) *vcfauto.Client
		wantStatus int
	}{
		{
			name:       "precheck rejects the token",
			opts:       mockapi.Options{Deployments: seed(action(actPowerOff, true))},
			client:     func(t *testing.T, url string) *vcfauto.Client { return clientWithToken(t, url, "wrong-token") },
			wantStatus: http.StatusUnauthorized,
		},
		{
			name:       "precheck fails",
			opts:       mockapi.Options{Deployments: seed(action(actPowerOff, true)), ActionsStatus: http.StatusInternalServerError},
			wantStatus: http.StatusInternalServerError,
		},
		{
			name:       "deployment does not exist",
			opts:       mockapi.Options{Deployments: map[string]mockapi.Deployment{}},
			wantStatus: http.StatusNotFound,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, tc.opts)
			var c *vcfauto.Client
			if tc.client != nil {
				c = tc.client(t, srv.URL())
			} else {
				c = newClient(t, srv.URL())
			}

			_, err := c.SubmitAction(context.Background(), vcfauto.ActionRequest{
				DeploymentID: depID,
				ActionID:     actPowerOff,
			})

			var apiErr *vcfauto.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("SubmitAction error = %v, want a *vcfauto.APIError", err)
			}
			if apiErr.StatusCode != tc.wantStatus {
				t.Errorf("APIError.StatusCode = %d, want %d", apiErr.StatusCode, tc.wantStatus)
			}
			if apiErr.Op != precheckOpID {
				t.Errorf("APIError.Op = %q, want %q", apiErr.Op, precheckOpID)
			}
			if n := countMutating(srv.Requests()); n != 0 {
				t.Errorf("the precheck failed but %d mutating request(s) were sent", n)
			}
		})
	}
}

func clientWithToken(t *testing.T, baseURL, token string) *vcfauto.Client {
	t.Helper()
	c, err := vcfauto.New(vcfauto.Config{BaseURL: baseURL, Token: token})
	if err != nil {
		t.Fatalf("vcfauto.New: %v", err)
	}
	return c
}

func TestMutatingFailureIsReportedAgainstTheMutatingOperation(t *testing.T) {
	srv := startMock(t, mockapi.Options{
		Deployments:   seed(action(actPowerOff, true)),
		RequestStatus: http.StatusConflict,
	})
	c := newClient(t, srv.URL())

	_, err := c.SubmitAction(context.Background(), vcfauto.ActionRequest{
		DeploymentID: depID,
		ActionID:     actPowerOff,
	})

	var apiErr *vcfauto.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("SubmitAction error = %v, want a *vcfauto.APIError", err)
	}
	if apiErr.StatusCode != http.StatusConflict {
		t.Errorf("APIError.StatusCode = %d, want %d", apiErr.StatusCode, http.StatusConflict)
	}
	if apiErr.Op != mutatingOpID {
		t.Errorf("APIError.Op = %q, want %q", apiErr.Op, mutatingOpID)
	}
	if n := len(srv.Requests()); n != 2 {
		t.Errorf("recorded %d requests, want the precheck read then the mutating call", n)
	}
}

func TestNewRejectsAnEmptyConfig(t *testing.T) {
	for _, tc := range []struct {
		name string
		cfg  vcfauto.Config
	}{
		{"no base url", vcfauto.Config{Token: testToken}},
		{"no token", vcfauto.Config{BaseURL: "https://vcf.example.test"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := vcfauto.New(tc.cfg); err == nil {
				t.Fatal("New returned a nil error")
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Concurrency
// ---------------------------------------------------------------------------

// TestConcurrentSubmitAction runs under -race and exercises the request log.
func TestConcurrentSubmitAction(t *testing.T) {
	const n = 8

	deployments := map[string]mockapi.Deployment{}
	for i := 0; i < n; i++ {
		deployments[fmt.Sprintf("dep-%02d", i)] = mockapi.Deployment{
			Actions: []mockapi.Action{action(actPowerOff, true)},
		}
	}
	srv := startMock(t, mockapi.Options{Deployments: deployments})
	c := newClient(t, srv.URL())

	var wg sync.WaitGroup
	errs := make([]error, n)
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, err := c.SubmitAction(context.Background(), vcfauto.ActionRequest{
				DeploymentID: fmt.Sprintf("dep-%02d", i),
				ActionID:     actPowerOff,
				Reason:       fmt.Sprintf("run %d", i),
			})
			errs[i] = err
		}(i)
	}
	wg.Wait()

	for i, err := range errs {
		if err != nil {
			t.Errorf("SubmitAction %d: %v", i, err)
		}
	}
	if got := len(srv.Requests()); got != 2*n {
		t.Errorf("recorded %d requests, want %d", got, 2*n)
	}
	if got := countMutating(srv.Requests()); got != n {
		t.Errorf("recorded %d mutating requests, want %d", got, n)
	}
}

// TestRequestsReturnsACopy keeps the log readable without handing out the
// server's own state: a caller poking at what it read must not rewrite history.
func TestRequestsReturnsACopy(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: seed(action(actPowerOff, true))})
	c := newClient(t, srv.URL())

	if _, err := c.SubmitAction(context.Background(), vcfauto.ActionRequest{
		DeploymentID: depID,
		ActionID:     actPowerOff,
		Reason:       "original",
	}); err != nil {
		t.Fatalf("SubmitAction: %v", err)
	}

	first := srv.Requests()
	if len(first) != 2 {
		t.Fatalf("recorded %v, want 2 requests", methods(first))
	}
	post := first[1]
	if len(post.Body) == 0 {
		t.Fatal("the mutating request was recorded with an empty body")
	}
	wantPath := post.Path
	wantBody := string(post.Body)
	wantAuth := post.Header.Get("Authorization")

	// Tamper with everything the caller was handed.
	first[1].Method = "TRACE"
	first[1].Path = "/tampered"
	first[1].Body[0] = 'X'
	first[1].Header.Set("Authorization", "Bearer tampered")
	first = first[:1]

	second := srv.Requests()
	if len(second) != 2 {
		t.Fatalf("recorded %v after tampering, want 2 requests", methods(second))
	}
	got := second[1]
	if got.Method != http.MethodPost || got.Path != wantPath {
		t.Errorf("log entry = %s %s, want %s %s", got.Method, got.Path, http.MethodPost, wantPath)
	}
	if string(got.Body) != wantBody {
		t.Errorf("logged body = %q, want %q: Requests must copy the body, not alias it", got.Body, wantBody)
	}
	if h := got.Header.Get("Authorization"); h != wantAuth {
		t.Errorf("logged Authorization = %q, want %q: Requests must clone the header", h, wantAuth)
	}
}

// ---------------------------------------------------------------------------
// Constructor and complete mock contract
// ---------------------------------------------------------------------------

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestNewValidationAndTrailingSlashNormalization(t *testing.T) {
	tests := []struct {
		name string
		cfg  vcfauto.Config
	}{
		{"empty base url", vcfauto.Config{Token: testToken}},
		{"relative base url", vcfauto.Config{BaseURL: "vcf.example.test", Token: testToken}},
		{"empty token", vcfauto.Config{BaseURL: "https://vcf.example.test"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			_, err := vcfauto.New(tc.cfg)
			if !errors.Is(err, vcfauto.ErrInvalidRequest) {
				t.Fatalf("New error = %v, want errors.Is(err, ErrInvalidRequest)", err)
			}
		})
	}

	var gotURL string
	hc := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		gotURL = r.URL.String()
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     make(http.Header),
			Body:       ioNopCloserString("[]"),
			Request:    r,
		}, nil
	})}
	c, err := vcfauto.New(vcfauto.Config{
		BaseURL:    "https://vcf.example.test///",
		Token:      testToken,
		HTTPClient: hc,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if _, err := c.ListActions(context.Background(), depID); err != nil {
		t.Fatalf("ListActions: %v", err)
	}
	wantURL := "https://vcf.example.test/deployment/api/deployments/" + depID + "/actions"
	if gotURL != wantURL {
		t.Errorf("request URL = %q, want %q", gotURL, wantURL)
	}
}

type stringReadCloser struct{ *strings.Reader }

func (stringReadCloser) Close() error { return nil }

func ioNopCloserString(value string) stringReadCloser {
	return stringReadCloser{Reader: strings.NewReader(value)}
}

func writeContractFixture(t *testing.T, contents string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "contract.json")
	if err := os.WriteFile(path, []byte(contents), 0o644); err != nil {
		t.Fatalf("write contract fixture: %v", err)
	}
	return path
}

func TestMockStartRejectsInvalidConfigurationAndContracts(t *testing.T) {
	tests := []struct {
		name      string
		opts      mockapi.Options
		wantInErr string
	}{
		{"empty contract path", mockapi.Options{Token: testToken}, "ContractPath"},
		{"empty token", mockapi.Options{ContractPath: contractPath}, "Token"},
		{"unreadable contract", mockapi.Options{ContractPath: filepath.Join(t.TempDir(), "missing.json"), Token: testToken}, "read contract"},
		{"malformed contract", mockapi.Options{ContractPath: writeContractFixture(t, `{`), Token: testToken}, "parse contract"},
		{"no operations", mockapi.Options{ContractPath: writeContractFixture(t, `{"operations":[]}`), Token: testToken}, "no operations"},
		{"unsupported operation", mockapi.Options{ContractPath: writeContractFixture(t, `{"operations":[{"id":"deleteDeployment","method":"DELETE","path":"/deployment/api/deployments/{deploymentId}"}]}`), Token: testToken}, "deleteDeployment"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv, err := mockapi.Start(tc.opts)
			if err == nil {
				srv.Close()
				t.Fatal("Start returned a nil error")
			}
			if !strings.Contains(err.Error(), tc.wantInErr) {
				t.Errorf("Start error = %q, want it to contain %q", err, tc.wantInErr)
			}
		})
	}
}

func directMockRequest(t *testing.T, srv *mockapi.Server, method, path, contentType string, body []byte, token string) (int, map[string]any) {
	t.Helper()
	var reader *bytes.Reader
	if body != nil {
		reader = bytes.NewReader(body)
	} else {
		reader = bytes.NewReader(nil)
	}
	req, err := http.NewRequest(method, srv.URL()+path, reader)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do request: %v", err)
	}
	defer resp.Body.Close()
	var decoded map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&decoded); err != nil {
		t.Fatalf("decode status %d response: %v", resp.StatusCode, err)
	}
	return resp.StatusCode, decoded
}

func TestMockMutatingValidationAndWillingGate(t *testing.T) {
	tests := []struct {
		name        string
		deployment  string
		contentType string
		body        string
		wantStatus  int
	}{
		{"unknown deployment", "missing", "application/json", `{"actionId":"anything"}`, http.StatusNotFound},
		{"wrong content type", depID, "text/plain", `{"actionId":"anything"}`, http.StatusUnsupportedMediaType},
		{"malformed json", depID, "application/json", `not-json`, http.StatusBadRequest},
		{"array is not an object", depID, "application/json", `[]`, http.StatusBadRequest},
		{"null is not an object", depID, "application/json", `null`, http.StatusBadRequest},
		{"missing actionId", depID, "application/json", `{"reason":"none"}`, http.StatusBadRequest},
		{"unavailable action is accepted by the mock", depID, "application/json", `{"actionId":"not-listed"}`, http.StatusOK},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, mockapi.Options{Deployments: seed(action(actPowerOff, false))})
			status, got := directMockRequest(t, srv, http.MethodPost,
				"/deployment/api/deployments/"+tc.deployment+"/requests",
				tc.contentType, []byte(tc.body), testToken)
			if status != tc.wantStatus {
				t.Fatalf("status = %d, want %d (response %#v)", status, tc.wantStatus, got)
			}
			if tc.wantStatus == http.StatusOK {
				if got["actionId"] != "not-listed" || got["deploymentId"] != depID || got["status"] != "PENDING" {
					t.Errorf("response = %#v, want the accepted deterministic request", got)
				}
			}
		})
	}
}

func TestMockGeneratedIDsAndStatusOverridesAreDeterministic(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: seed()})
	for i, wantID := range []string{"req-1", "req-2"} {
		status, got := directMockRequest(t, srv, http.MethodPost,
			"/deployment/api/deployments/"+depID+"/requests", "application/json",
			[]byte(`{"actionId":"anything"}`), testToken)
		if status != http.StatusOK {
			t.Fatalf("request %d status = %d, want 200", i, status)
		}
		if got["id"] != wantID {
			t.Errorf("request %d id = %v, want %q", i, got["id"], wantID)
		}
	}

	tests := []struct {
		name       string
		opts       mockapi.Options
		method     string
		path       string
		wantStatus int
	}{
		{"actions override normal 404", mockapi.Options{ActionsStatus: 418}, http.MethodGet, "/deployment/api/deployments/missing/actions", 418},
		{"request override normal 404", mockapi.Options{RequestStatus: 429}, http.MethodPost, "/deployment/api/deployments/missing/requests", 429},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			o := tc.opts
			o.Deployments = map[string]mockapi.Deployment{}
			s := startMock(t, o)
			status, _ := directMockRequest(t, s, tc.method, tc.path, "", nil, testToken)
			if status != tc.wantStatus {
				t.Errorf("status = %d, want injected %d", status, tc.wantStatus)
			}
		})
	}
}

func TestMockRecordsRejectedAndUnroutedRequestsInOrder(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: seed()})
	tests := []struct {
		method string
		path   string
		token  string
		want   int
	}{
		{http.MethodGet, "/deployment/api/deployments/" + depID + "/actions?from=test", "wrong", http.StatusUnauthorized},
		{http.MethodDelete, "/deployment/api/deployments/" + depID + "/actions", testToken, http.StatusMethodNotAllowed},
		{http.MethodPost, "/unrouted", testToken, http.StatusNotFound},
	}
	for _, tc := range tests {
		status, _ := directMockRequest(t, srv, tc.method, tc.path, "application/json", []byte(`{"x":1}`), tc.token)
		if status != tc.want {
			t.Fatalf("%s %s status = %d, want %d", tc.method, tc.path, status, tc.want)
		}
	}
	recs := srv.Requests()
	if len(recs) != len(tests) {
		t.Fatalf("recorded %d requests, want %d", len(recs), len(tests))
	}
	for i, tc := range tests {
		if recs[i].Method != tc.method || recs[i].Path != strings.Split(tc.path, "?")[0] {
			t.Errorf("record %d = %s %s, want %s %s", i, recs[i].Method, recs[i].Path, tc.method, tc.path)
		}
		if string(recs[i].Body) != `{"x":1}` {
			t.Errorf("record %d body = %q", i, recs[i].Body)
		}
	}
	if recs[0].RawQuery != "from=test" {
		t.Errorf("first RawQuery = %q, want from=test", recs[0].RawQuery)
	}
}
