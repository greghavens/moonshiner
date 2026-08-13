package mockops_test

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"vcfops.local/opsreport/internal/contract"
	"vcfops.local/opsreport/internal/mockops"
)

// These tests exercise the mock itself so a failure in the wire verifier can be
// attributed to the client under test rather than to the fixture.

func TestMockServesOnlyContractOperations(t *testing.T) {
	t.Parallel()
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	srv := mockops.StartWithContract(t, c, mockops.Scenario{})

	tests := []struct {
		name   string
		method string
		path   string
		want   int
	}{
		{"contract operation is served", http.MethodPost, "/suite-api/api/auth/token/acquire", http.StatusUnauthorized},
		{"sibling operation outside contract", http.MethodGet, "/suite-api/api/reportdefinitions", http.StatusNotFound},
		{"delete report is outside contract", http.MethodDelete, "/suite-api/api/reports/" + srv.Scenario().ReportID, http.StatusNotFound},
		{"list reports is outside contract", http.MethodGet, "/suite-api/api/reports", http.StatusNotFound},
		{"log management path is a different API", http.MethodGet, "/suite-api/api/logs", http.StatusNotFound},
		{"path without the base path", http.MethodPost, "/api/reports", http.StatusNotFound},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, srv.URL()+tc.path, strings.NewReader("{}"))
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			resp, err := srv.Client().Do(req)
			if err != nil {
				t.Fatalf("do request: %v", err)
			}
			defer resp.Body.Close()
			_, _ = io.Copy(io.Discard, resp.Body)
			if resp.StatusCode != tc.want {
				t.Errorf("%s %s: got status %d, want %d", tc.method, tc.path, resp.StatusCode, tc.want)
			}
		})
	}
}

func TestMockRecordsRequestsInOrder(t *testing.T) {
	t.Parallel()
	srv := mockops.Start(t, mockops.Scenario{})
	sc := srv.Scenario()

	body, err := json.Marshal(map[string]any{"username": sc.Username, "password": sc.Password})
	if err != nil {
		t.Fatalf("marshal credentials: %v", err)
	}
	resp, err := srv.Client().Post(srv.URL()+"/suite-api/api/auth/token/acquire", "application/json", strings.NewReader(string(body)))
	if err != nil {
		t.Fatalf("acquire token: %v", err)
	}
	defer resp.Body.Close()
	var tok struct {
		Token string `json:"token"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tok); err != nil {
		t.Fatalf("decode token: %v", err)
	}
	if tok.Token != sc.Token {
		t.Fatalf("token = %q, want %q", tok.Token, sc.Token)
	}

	// A path the contract does not name.
	unmatched, err := srv.Client().Get(srv.URL() + "/suite-api/api/solutions")
	if err != nil {
		t.Fatalf("get unmatched: %v", err)
	}
	defer unmatched.Body.Close()
	_, _ = io.Copy(io.Discard, unmatched.Body)

	got := srv.Requests()
	if len(got) != 2 {
		t.Fatalf("recorded %d requests, want 2", len(got))
	}
	if got[0].Index != 0 || got[0].OperationID != "acquireToken" || got[0].ResponseStatus != http.StatusOK {
		t.Errorf("request 0 = %+v, want index 0 acquireToken 200", got[0])
	}
	if got[1].Index != 1 || got[1].OperationID != "" || got[1].ResponseStatus != http.StatusNotFound {
		t.Errorf("request 1 = %+v, want index 1 unmatched 404", got[1])
	}

	keys, err := got[0].BodyKeys()
	if err != nil {
		t.Fatalf("body keys: %v", err)
	}
	if strings.Join(keys, ",") != "password,username" {
		t.Errorf("acquireToken body keys = %v, want [password username]", keys)
	}
}

func TestMockRequiresPollingBeforeDownload(t *testing.T) {
	t.Parallel()
	srv := mockops.Start(t, mockops.Scenario{PollStatuses: []string{"RUNNING", "COMPLETED"}})
	sc := srv.Scenario()
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}

	download := func() int {
		req, err := http.NewRequest(http.MethodGet, srv.URL()+"/suite-api/api/reports/"+sc.ReportID+"/download", nil)
		if err != nil {
			t.Fatalf("build download: %v", err)
		}
		req.Header.Set(c.Authorization.HeaderName, c.AuthHeaderValue(sc.Token))
		resp, err := srv.Client().Do(req)
		if err != nil {
			t.Fatalf("download: %v", err)
		}
		defer resp.Body.Close()
		_, _ = io.Copy(io.Discard, resp.Body)
		return resp.StatusCode
	}

	poll := func() string {
		req, err := http.NewRequest(http.MethodGet, srv.URL()+"/suite-api/api/reports/"+sc.ReportID, nil)
		if err != nil {
			t.Fatalf("build poll: %v", err)
		}
		req.Header.Set(c.Authorization.HeaderName, c.AuthHeaderValue(sc.Token))
		resp, err := srv.Client().Do(req)
		if err != nil {
			t.Fatalf("poll: %v", err)
		}
		defer resp.Body.Close()
		var r struct {
			Status string `json:"status"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
			t.Fatalf("decode poll: %v", err)
		}
		return r.Status
	}

	if got := download(); got != http.StatusConflict {
		t.Errorf("download before polling: got %d, want %d", got, http.StatusConflict)
	}
	if got := poll(); got != "RUNNING" {
		t.Errorf("first poll = %q, want RUNNING", got)
	}
	if got := download(); got != http.StatusConflict {
		t.Errorf("download while RUNNING: got %d, want %d", got, http.StatusConflict)
	}
	if got := poll(); got != "COMPLETED" {
		t.Errorf("second poll = %q, want COMPLETED", got)
	}
	if got := download(); got != http.StatusOK {
		t.Errorf("download after COMPLETED: got %d, want %d", got, http.StatusOK)
	}
	if got := poll(); got != "COMPLETED" {
		t.Errorf("third poll = %q, want COMPLETED (last status repeats)", got)
	}
}

func TestMockRejectsUnauthorizedAndServerPopulatedFields(t *testing.T) {
	t.Parallel()
	srv := mockops.Start(t, mockops.Scenario{})
	sc := srv.Scenario()
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}

	tests := []struct {
		name  string
		token string
		body  map[string]any
		want  int
	}{
		{
			name:  "missing token",
			token: "",
			body:  map[string]any{"reportDefinitionId": "a", "resourceId": "b"},
			want:  http.StatusUnauthorized,
		},
		{
			name:  "required property absent",
			token: sc.Token,
			body:  map[string]any{"reportDefinitionId": "a"},
			want:  http.StatusBadRequest,
		},
		{
			name:  "required property empty",
			token: sc.Token,
			body:  map[string]any{"reportDefinitionId": "a", "resourceId": ""},
			want:  http.StatusBadRequest,
		},
		{
			name:  "server populated property sent",
			token: sc.Token,
			body:  map[string]any{"reportDefinitionId": "a", "resourceId": "b", "status": "COMPLETED"},
			want:  http.StatusBadRequest,
		},
		{
			name:  "accepted",
			token: sc.Token,
			body:  map[string]any{"reportDefinitionId": "a", "resourceId": "b"},
			want:  http.StatusOK,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			raw, err := json.Marshal(tc.body)
			if err != nil {
				t.Fatalf("marshal body: %v", err)
			}
			req, err := http.NewRequest(http.MethodPost, srv.URL()+"/suite-api/api/reports", strings.NewReader(string(raw)))
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			req.Header.Set("Content-Type", "application/json")
			if tc.token != "" {
				req.Header.Set(c.Authorization.HeaderName, c.AuthHeaderValue(tc.token))
			}
			resp, err := srv.Client().Do(req)
			if err != nil {
				t.Fatalf("do request: %v", err)
			}
			defer resp.Body.Close()
			_, _ = io.Copy(io.Discard, resp.Body)
			if resp.StatusCode != tc.want {
				t.Errorf("got status %d, want %d", resp.StatusCode, tc.want)
			}
		})
	}
}
