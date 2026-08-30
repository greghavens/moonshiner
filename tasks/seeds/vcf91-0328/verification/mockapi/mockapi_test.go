package mockapi_test

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"example.com/vcfauto/mockapi"
)

const (
	contractPath = "../docs/contract.json"
	token        = "mock-test-token"
	deployment   = "3f9a1c77-55b2-4e0a-9d61-8ab4c2e07f13"
)

func start(t *testing.T, opts mockapi.Options) *mockapi.Server {
	t.Helper()
	if opts.ContractPath == "" {
		opts.ContractPath = contractPath
	}
	if opts.Token == "" {
		opts.Token = token
	}
	if opts.Deployments == nil {
		opts.Deployments = map[string]mockapi.Deployment{
			deployment: {Actions: []mockapi.Action{{ID: "Deployment.PowerOff", Valid: true}}},
		}
	}
	srv, err := mockapi.Start(opts)
	if err != nil {
		t.Fatalf("Start: %v", err)
	}
	t.Cleanup(srv.Close)
	return srv
}

func do(t *testing.T, srv *mockapi.Server, method, path, body, authToken string) *http.Response {
	t.Helper()
	var rdr *strings.Reader
	if body != "" {
		rdr = strings.NewReader(body)
	}
	var req *http.Request
	var err error
	if rdr == nil {
		req, err = http.NewRequest(method, srv.URL()+path, nil)
	} else {
		req, err = http.NewRequest(method, srv.URL()+path, rdr)
		req.Header.Set("Content-Type", "application/json")
	}
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	if authToken != "" {
		req.Header.Set("Authorization", "Bearer "+authToken)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func TestStartValidatesOptions(t *testing.T) {
	tests := []struct {
		name string
		opts mockapi.Options
	}{
		{"no contract path", mockapi.Options{Token: token}},
		{"no token", mockapi.Options{ContractPath: contractPath}},
		{"contract does not exist", mockapi.Options{ContractPath: "does/not/exist.json", Token: token}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv, err := mockapi.Start(tc.opts)
			if err == nil {
				srv.Close()
				t.Fatal("Start returned a nil error")
			}
		})
	}
}

func TestStartRejectsAnUnknownOperation(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "contract.json")
	doc := `{"operations":[{"id":"deleteDeployment","method":"DELETE","path":"/deployment/api/deployments/{deploymentId}"}]}`
	if err := os.WriteFile(path, []byte(doc), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	srv, err := mockapi.Start(mockapi.Options{ContractPath: path, Token: token})
	if err == nil {
		srv.Close()
		t.Fatal("Start served an operation the mock does not implement")
	}
	if !strings.Contains(err.Error(), "deleteDeployment") {
		t.Errorf("error = %v, want it to name the unserved operation", err)
	}
}

func TestRouting(t *testing.T) {
	tests := []struct {
		name       string
		method     string
		path       string
		body       string
		token      string
		wantStatus int
	}{
		{"precheck", http.MethodGet, "/deployment/api/deployments/" + deployment + "/actions", "", token, http.StatusOK},
		{"precheck unknown deployment", http.MethodGet, "/deployment/api/deployments/nope/actions", "", token, http.StatusNotFound},
		{"precheck without a token", http.MethodGet, "/deployment/api/deployments/" + deployment + "/actions", "", "", http.StatusUnauthorized},
		{"precheck with the wrong token", http.MethodGet, "/deployment/api/deployments/" + deployment + "/actions", "", "nope", http.StatusUnauthorized},
		{"submit", http.MethodPost, "/deployment/api/deployments/" + deployment + "/requests", `{"actionId":"Deployment.PowerOff"}`, token, http.StatusOK},
		{"submit without an actionId", http.MethodPost, "/deployment/api/deployments/" + deployment + "/requests", `{}`, token, http.StatusBadRequest},
		{"submit with a malformed body", http.MethodPost, "/deployment/api/deployments/" + deployment + "/requests", `not json`, token, http.StatusBadRequest},
		{"wrong method on a contract path", http.MethodDelete, "/deployment/api/deployments/" + deployment + "/actions", "", token, http.StatusMethodNotAllowed},
		{"operation outside the contract", http.MethodGet, "/deployment/api/requests/req-1", "", token, http.StatusNotFound},
		{"unrelated path", http.MethodGet, "/", "", token, http.StatusNotFound},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := start(t, mockapi.Options{})
			resp := do(t, srv, tc.method, tc.path, tc.body, tc.token)
			if resp.StatusCode != tc.wantStatus {
				t.Errorf("%s %s = %d, want %d", tc.method, tc.path, resp.StatusCode, tc.wantStatus)
			}
		})
	}
}

func TestInjectedStatuses(t *testing.T) {
	tests := []struct {
		name       string
		opts       mockapi.Options
		method     string
		path       string
		body       string
		wantStatus int
	}{
		{
			name:       "precheck failure",
			opts:       mockapi.Options{ActionsStatus: http.StatusInternalServerError},
			method:     http.MethodGet,
			path:       "/deployment/api/deployments/" + deployment + "/actions",
			wantStatus: http.StatusInternalServerError,
		},
		{
			name:       "mutating failure",
			opts:       mockapi.Options{RequestStatus: http.StatusConflict},
			method:     http.MethodPost,
			path:       "/deployment/api/deployments/" + deployment + "/requests",
			body:       `{"actionId":"Deployment.PowerOff"}`,
			wantStatus: http.StatusConflict,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := start(t, tc.opts)
			resp := do(t, srv, tc.method, tc.path, tc.body, token)
			if resp.StatusCode != tc.wantStatus {
				t.Errorf("status = %d, want %d", resp.StatusCode, tc.wantStatus)
			}
		})
	}
}

func TestSubmitRequiresJSONContentType(t *testing.T) {
	srv := start(t, mockapi.Options{})

	req, err := http.NewRequest(http.MethodPost,
		srv.URL()+"/deployment/api/deployments/"+deployment+"/requests",
		strings.NewReader(`{"actionId":"Deployment.PowerOff"}`))
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "text/plain")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnsupportedMediaType {
		t.Errorf("status = %d, want 415", resp.StatusCode)
	}
}

func TestRequestLog(t *testing.T) {
	srv := start(t, mockapi.Options{})

	do(t, srv, http.MethodGet, "/deployment/api/deployments/"+deployment+"/actions?ignored=1", "", token)
	do(t, srv, http.MethodPost, "/deployment/api/deployments/"+deployment+"/requests", `{"actionId":"Deployment.PowerOff"}`, token)

	recs := srv.Requests()
	if len(recs) != 2 {
		t.Fatalf("recorded %d requests, want 2", len(recs))
	}

	tests := []struct {
		name     string
		rec      mockapi.Recorded
		method   string
		path     string
		rawQuery string
		body     string
	}{
		{"precheck", recs[0], http.MethodGet, "/deployment/api/deployments/" + deployment + "/actions", "ignored=1", ""},
		{"submit", recs[1], http.MethodPost, "/deployment/api/deployments/" + deployment + "/requests", "", `{"actionId":"Deployment.PowerOff"}`},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if tc.rec.Method != tc.method {
				t.Errorf("Method = %q, want %q", tc.rec.Method, tc.method)
			}
			if tc.rec.Path != tc.path {
				t.Errorf("Path = %q, want %q", tc.rec.Path, tc.path)
			}
			if tc.rec.RawQuery != tc.rawQuery {
				t.Errorf("RawQuery = %q, want %q", tc.rec.RawQuery, tc.rawQuery)
			}
			if string(tc.rec.Body) != tc.body {
				t.Errorf("Body = %q, want %q", tc.rec.Body, tc.body)
			}
			if tc.rec.Header.Get("Authorization") != "Bearer "+token {
				t.Errorf("Authorization was not recorded")
			}
		})
	}
}

func TestUnauthorizedRequestsAreStillLogged(t *testing.T) {
	srv := start(t, mockapi.Options{})

	do(t, srv, http.MethodGet, "/deployment/api/deployments/"+deployment+"/actions", "", "")

	if n := len(srv.Requests()); n != 1 {
		t.Errorf("recorded %d requests, want the rejected one to be logged too", n)
	}
}

func TestPrecheckResponseShape(t *testing.T) {
	srv := start(t, mockapi.Options{
		Deployments: map[string]mockapi.Deployment{
			deployment: {Actions: []mockapi.Action{
				{ID: "Deployment.PowerOff", Name: "PowerOff", DisplayName: "Power Off", ActionType: "RESOURCE_ACTION", Valid: true},
			}},
		},
	})

	resp := do(t, srv, http.MethodGet, "/deployment/api/deployments/"+deployment+"/actions", "", token)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}

	var got []map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&got); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d actions, want 1", len(got))
	}
	for _, key := range []string{"id", "name", "displayName", "actionType", "valid"} {
		if _, ok := got[0][key]; !ok {
			t.Errorf("action is missing %q", key)
		}
	}
	if got[0]["valid"] != true {
		t.Errorf("valid = %v, want true", got[0]["valid"])
	}
}
