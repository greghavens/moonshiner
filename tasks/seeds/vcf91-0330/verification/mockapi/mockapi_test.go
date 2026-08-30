package mockapi_test

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"example.com/vcfdiag/mockapi"
)

const testToken = "tkn-mock-test"

func contractPath() string { return filepath.Join("..", "docs", "contract.json") }

func seed() map[string]mockapi.Deployment {
	return map[string]mockapi.Deployment{
		"dep-1": {Requests: []mockapi.Request{
			{ID: "r-1", Name: "Update", Status: "FAILED", Details: "Request failed.", Events: []mockapi.Event{
				{ID: "e-1", Name: "Allocate", Timestamp: "2026-01-01T00:00:00Z", HasLogs: true,
					Logs: []string{"one", "two", "three", "four", "five"}},
				{ID: "e-2", Name: "Notify", Timestamp: "2026-01-01T00:01:00Z", UserEvent: true},
			}},
		}},
	}
}

func start(t *testing.T, opts mockapi.Options) *mockapi.Server {
	t.Helper()
	if opts.ContractPath == "" {
		opts.ContractPath = contractPath()
	}
	if opts.Token == "" {
		opts.Token = testToken
	}
	srv, err := mockapi.Start(opts)
	if err != nil {
		t.Fatalf("Start: %v", err)
	}
	t.Cleanup(srv.Close)
	return srv
}

func get(t *testing.T, target, auth string) (*http.Response, []byte) {
	t.Helper()
	req, err := http.NewRequest("GET", target, nil)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	if auth != "" {
		req.Header.Set("Authorization", auth)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	defer resp.Body.Close()
	body := make([]byte, 0)
	buf := make([]byte, 4096)
	for {
		n, err := resp.Body.Read(buf)
		body = append(body, buf[:n]...)
		if err != nil {
			break
		}
	}
	return resp, body
}

func TestStartValidation(t *testing.T) {
	badJSON := filepath.Join(t.TempDir(), "bad.json")
	if err := os.WriteFile(badJSON, []byte("nope"), 0o644); err != nil {
		t.Fatal(err)
	}
	empty := writeContract(t, []map[string]any{})
	unknown := writeContract(t, []map[string]any{
		{"id": "getCatalogItems", "method": "GET", "path": "/catalog/api/items"},
	})
	wrongPath := writeContract(t, []map[string]any{
		{"id": "getRequest", "method": "GET", "path": "/deployment/api/request/{requestId}"},
	})

	cases := []struct {
		name     string
		opts     mockapi.Options
		wantErr  bool
		contains string
	}{
		{"ok", mockapi.Options{ContractPath: contractPath(), Token: testToken}, false, ""},
		{"no contract path", mockapi.Options{Token: testToken}, true, "ContractPath"},
		{"no token", mockapi.Options{ContractPath: contractPath()}, true, "Token"},
		{"missing file", mockapi.Options{ContractPath: "no/such.json", Token: testToken}, true, ""},
		{"not json", mockapi.Options{ContractPath: badJSON, Token: testToken}, true, ""},
		{"no operations", mockapi.Options{ContractPath: empty, Token: testToken}, true, "no operations"},
		{"unserved operation", mockapi.Options{ContractPath: unknown, Token: testToken}, true, "getCatalogItems"},
		{"wrong path for operation", mockapi.Options{ContractPath: wrongPath, Token: testToken}, true, "getRequest"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv, err := mockapi.Start(tc.opts)
			if srv != nil {
				defer srv.Close()
			}
			if tc.wantErr {
				if err == nil {
					t.Fatal("want an error, got none")
				}
				if tc.contains != "" && !strings.Contains(err.Error(), tc.contains) {
					t.Errorf("error %q does not contain %q", err, tc.contains)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if !strings.HasPrefix(srv.URL(), "http://127.0.0.1:") {
				t.Errorf("URL = %q, want a loopback address", srv.URL())
			}
		})
	}
}

func TestRouting(t *testing.T) {
	srv := start(t, mockapi.Options{Deployments: seed(), LogPageSize: 2})
	auth := "Bearer " + testToken

	cases := []struct {
		name   string
		target string
		auth   string
		want   int
	}{
		{"deployment requests", "/deployment/api/deployments/dep-1/requests", auth, 200},
		{"request", "/deployment/api/requests/r-1", auth, 200},
		{"events", "/deployment/api/requests/r-1/events", auth, 200},
		{"logs", "/deployment/api/requests/r-1/events/e-1/logs", auth, 200},
		{"unknown deployment", "/deployment/api/deployments/nope/requests", auth, 404},
		{"unknown request", "/deployment/api/requests/nope", auth, 404},
		{"unknown event", "/deployment/api/requests/r-1/events/nope/logs", auth, 404},
		{"event without logs", "/deployment/api/requests/r-1/events/e-2/logs", auth, 404},
		{"unrouted", "/deployment/api/resources", auth, 404},
		{"no auth", "/deployment/api/requests/r-1", "", 401},
		{"bad auth", "/deployment/api/requests/r-1", "Bearer wrong", 401},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resp, _ := get(t, srv.URL()+tc.target, tc.auth)
			if resp.StatusCode != tc.want {
				t.Errorf("GET %s = %d, want %d", tc.target, resp.StatusCode, tc.want)
			}
		})
	}

	t.Run("wrong method on a routed path", func(t *testing.T) {
		req, err := http.NewRequest("DELETE", srv.URL()+"/deployment/api/requests/r-1", nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", auth)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusMethodNotAllowed {
			t.Errorf("status = %d, want 405", resp.StatusCode)
		}
	})
}

func TestLogSlicing(t *testing.T) {
	cases := []struct {
		name        string
		pageSize    int
		sinceRow    string
		wantRows    []int
		wantLast    bool
		wantEOFLast bool
	}{
		{"first page of two", 2, "", []int{1, 2}, false, false},
		{"second page of two", 2, "3", []int{3, 4}, false, false},
		{"final partial page", 2, "5", []int{5}, true, true},
		{"whole log at once", 0, "", []int{1, 2, 3, 4, 5}, true, true},
		{"sinceRow past the end", 2, "9", nil, true, false},
		{"sinceRow of one is the top", 2, "1", []int{1, 2}, false, false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := start(t, mockapi.Options{Deployments: seed(), LogPageSize: tc.pageSize})
			target := srv.URL() + "/deployment/api/requests/r-1/events/e-1/logs"
			if tc.sinceRow != "" {
				target += "?sinceRow=" + tc.sinceRow
			}
			resp, body := get(t, target, "Bearer "+testToken)
			if resp.StatusCode != 200 {
				t.Fatalf("status = %d, want 200", resp.StatusCode)
			}

			var slice struct {
				Content []struct {
					Rownum int    `json:"rownum"`
					EOF    bool   `json:"eof"`
					ID     string `json:"id"`
				} `json:"content"`
				Last bool `json:"last"`
			}
			if err := json.Unmarshal(body, &slice); err != nil {
				t.Fatalf("decode: %v (%s)", err, body)
			}

			var gotRows []int
			for _, l := range slice.Content {
				gotRows = append(gotRows, l.Rownum)
			}
			if len(gotRows) != len(tc.wantRows) {
				t.Fatalf("rows = %v, want %v", gotRows, tc.wantRows)
			}
			for i, want := range tc.wantRows {
				if gotRows[i] != want {
					t.Errorf("rows = %v, want %v", gotRows, tc.wantRows)
					break
				}
			}
			if slice.Last != tc.wantLast {
				t.Errorf("last = %v, want %v", slice.Last, tc.wantLast)
			}
			if tc.wantEOFLast && len(slice.Content) > 0 {
				if !slice.Content[len(slice.Content)-1].EOF {
					t.Error("final row is not marked eof")
				}
			}
			// Ids are derived from the event and row, never generated.
			for _, l := range slice.Content {
				if !strings.HasPrefix(l.ID, "e-1-log-") {
					t.Errorf("log id %q is not derived from the event and row", l.ID)
				}
			}
		})
	}
}

func TestStatusOverrides(t *testing.T) {
	cases := []struct {
		name   string
		opts   mockapi.Options
		target string
		want   int
	}{
		{"requests", mockapi.Options{RequestsStatus: 503}, "/deployment/api/deployments/dep-1/requests", 503},
		{"request", mockapi.Options{RequestStatus: 500}, "/deployment/api/requests/r-1", 500},
		{"events", mockapi.Options{EventsStatus: 502}, "/deployment/api/requests/r-1/events", 502},
		{"logs", mockapi.Options{LogsStatus: 418}, "/deployment/api/requests/r-1/events/e-1/logs", 418},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			opts := tc.opts
			opts.Deployments = seed()
			srv := start(t, opts)
			resp, _ := get(t, srv.URL()+tc.target, "Bearer "+testToken)
			if resp.StatusCode != tc.want {
				t.Errorf("status = %d, want %d", resp.StatusCode, tc.want)
			}
		})
	}
}

func TestRequestLog(t *testing.T) {
	srv := start(t, mockapi.Options{Deployments: seed(), LogPageSize: 2})

	get(t, srv.URL()+"/deployment/api/requests/r-1", "Bearer "+testToken)
	get(t, srv.URL()+"/deployment/api/requests/r-1/events?size=7&sort=timestamp,ASC", "Bearer "+testToken)
	get(t, srv.URL()+"/unrouted", "")

	recs := srv.Requests()
	if len(recs) != 3 {
		t.Fatalf("recorded %d requests, want 3 (unrouted and unauthorised requests are recorded too)", len(recs))
	}
	if recs[1].RawQuery != "size=7&sort=timestamp,ASC" {
		t.Errorf("RawQuery = %q, want the query verbatim", recs[1].RawQuery)
	}
	if recs[2].Path != "/unrouted" {
		t.Errorf("Path = %q, want /unrouted", recs[2].Path)
	}

	t.Run("the log is a copy", func(t *testing.T) {
		a := srv.Requests()
		a[0].Path = "/tampered"
		a[0].Header.Set("Authorization", "tampered")
		b := srv.Requests()
		if b[0].Path == "/tampered" {
			t.Error("Requests() shares its slice with the server")
		}
		if b[0].Header.Get("Authorization") == "tampered" {
			t.Error("Requests() shares its header map with the server")
		}
	})

	t.Run("concurrent reads and writes", func(t *testing.T) {
		var wg sync.WaitGroup
		for range 6 {
			wg.Add(2)
			go func() { defer wg.Done(); get(t, srv.URL()+"/deployment/api/requests/r-1", "Bearer "+testToken) }()
			go func() { defer wg.Done(); srv.Requests() }()
		}
		wg.Wait()
	})
}

func writeContract(t *testing.T, ops []map[string]any) string {
	t.Helper()
	raw, err := json.Marshal(map[string]any{
		"api":        map[string]any{"version": "9.1"},
		"operations": ops,
	})
	if err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(t.TempDir(), "contract.json")
	if err := os.WriteFile(p, raw, 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}
