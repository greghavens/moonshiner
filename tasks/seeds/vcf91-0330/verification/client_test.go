package vcfdiag_test

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"path/filepath"
	"strings"
	"testing"

	"example.com/vcfdiag"
	"example.com/vcfdiag/mockapi"
)

const testToken = "tkn-client-test"

func contractPath(t *testing.T) string {
	t.Helper()
	return filepath.Join("docs", "contract.json")
}

func seed() map[string]mockapi.Deployment {
	return map[string]mockapi.Deployment{
		"dep-1": {Requests: []mockapi.Request{
			{ID: "r-new", Name: "Power Off", Status: "SUCCESSFUL", CreatedAt: "2026-02-02T10:00:00Z"},
			{
				ID: "r-bad", Name: "Reconfigure", Status: "FAILED", Details: "Request failed.",
				CreatedAt: "2026-02-01T10:00:00Z",
				Events: []mockapi.Event{
					{
						ID: "e-1", Name: "Resize disk", Timestamp: "2026-02-01T10:00:10Z",
						ResourceName: "db-01", ResourceType: "Cloud.vSphere.Disk", HasLogs: true,
						Logs: []string{
							"INFO Resizing disk db-01 to 400 GB",
							"INFO Datastore ds-gold selected",
							"ERROR Resize refused: datastore ds-gold has 12 GB free, 150 GB needed",
							"INFO Task marked failed",
						},
					},
					{ID: "e-2", Name: "Notified owner", Timestamp: "2026-02-01T10:00:30Z", UserEvent: true},
					{
						ID: "e-3", Name: "Rollback", Timestamp: "2026-02-01T10:01:00Z",
						ResourceName: "db-01", HasLogs: true,
						Logs: []string{"ERROR Rollback left db-01 in ERROR state"},
					},
				},
			},
		}},
	}
}

func start(t *testing.T, opts mockapi.Options) *mockapi.Server {
	t.Helper()
	if opts.ContractPath == "" {
		opts.ContractPath = contractPath(t)
	}
	if opts.Token == "" {
		opts.Token = testToken
	}
	srv, err := mockapi.Start(opts)
	if err != nil {
		t.Fatalf("mockapi.Start: %v", err)
	}
	t.Cleanup(srv.Close)
	return srv
}

func TestNew(t *testing.T) {
	cases := []struct {
		name    string
		cfg     vcfdiag.Config
		wantErr bool
	}{
		{"ok", vcfdiag.Config{BaseURL: "https://vcfa.example.test", Token: "t"}, false},
		{"ok with trailing slash", vcfdiag.Config{BaseURL: "https://vcfa.example.test/", Token: "t"}, false},
		{"ok with custom http client", vcfdiag.Config{BaseURL: "https://vcfa.example.test", Token: "t", HTTPClient: &http.Client{}}, false},
		{"no base url", vcfdiag.Config{Token: "t"}, true},
		{"relative base url", vcfdiag.Config{BaseURL: "vcfa.example.test", Token: "t"}, true},
		{"scheme only", vcfdiag.Config{BaseURL: "https://", Token: "t"}, true},
		{"no token", vcfdiag.Config{BaseURL: "https://vcfa.example.test"}, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c, err := vcfdiag.New(tc.cfg)
			switch {
			case tc.wantErr && err == nil:
				t.Fatal("want an error, got none")
			case tc.wantErr && !errors.Is(err, vcfdiag.ErrInvalidRequest):
				t.Fatalf("err = %v, want ErrInvalidRequest", err)
			case !tc.wantErr && err != nil:
				t.Fatalf("unexpected error: %v", err)
			case !tc.wantErr && c == nil:
				t.Fatal("no client returned")
			}
		})
	}
}

func TestDiagnoseRootCause(t *testing.T) {
	cases := []struct {
		name        string
		logPageSize int
		wantCause   string
		wantEvent   string
		wantLines   int
		wantCalls   int
	}{
		{"one row per page", 1, "ERROR Resize refused: datastore ds-gold has 12 GB free, 150 GB needed", "e-1", 5, 3 + 4 + 1},
		{"two rows per page", 2, "ERROR Resize refused: datastore ds-gold has 12 GB free, 150 GB needed", "e-1", 5, 3 + 2 + 1},
		{"whole log in one page", 0, "ERROR Resize refused: datastore ds-gold has 12 GB free, 150 GB needed", "e-1", 5, 3 + 1 + 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := start(t, mockapi.Options{Deployments: seed(), LogPageSize: tc.logPageSize})
			c, err := vcfdiag.New(vcfdiag.Config{BaseURL: srv.URL(), Token: testToken})
			if err != nil {
				t.Fatalf("New: %v", err)
			}

			got, err := c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{DeploymentID: "dep-1"})
			if err != nil {
				t.Fatalf("Diagnose: %v", err)
			}
			if got.RootCause != tc.wantCause {
				t.Errorf("RootCause = %q, want %q", got.RootCause, tc.wantCause)
			}
			if got.EventID != tc.wantEvent {
				t.Errorf("EventID = %q, want %q", got.EventID, tc.wantEvent)
			}
			if got.RequestID != "r-bad" {
				t.Errorf("RequestID = %q, want r-bad", got.RequestID)
			}
			if len(got.LogLines) != tc.wantLines {
				t.Errorf("retrieved %d log lines, want %d", len(got.LogLines), tc.wantLines)
			}
			if n := len(srv.Requests()); n != tc.wantCalls {
				t.Errorf("made %d calls, want %d", n, tc.wantCalls)
			}
			for _, l := range got.LogLines {
				if l.EventID == "e-2" {
					t.Error("pulled logs for e-2, which has none")
				}
			}
		})
	}
}

func TestDiagnoseQueryShape(t *testing.T) {
	cases := []struct {
		name      string
		req       vcfdiag.DiagnoseRequest
		wantFirst map[string]string
	}{
		{"defaults", vcfdiag.DiagnoseRequest{DeploymentID: "dep-1"},
			map[string]string{"sort": "createdAt,DESC"}},
		{"with size", vcfdiag.DiagnoseRequest{DeploymentID: "dep-1", PageSize: 5},
			map[string]string{"sort": "createdAt,DESC", "size": "5"}},
		{"with search", vcfdiag.DiagnoseRequest{DeploymentID: "dep-1", Search: "db"},
			map[string]string{"sort": "createdAt,DESC", "search": "db"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := start(t, mockapi.Options{Deployments: seed(), LogPageSize: 2})
			c, err := vcfdiag.New(vcfdiag.Config{BaseURL: srv.URL(), Token: testToken})
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			if _, err := c.Diagnose(context.Background(), tc.req); err != nil {
				t.Fatalf("Diagnose: %v", err)
			}

			recs := srv.Requests()
			q, err := url.ParseQuery(recs[0].RawQuery)
			if err != nil {
				t.Fatalf("parse query: %v", err)
			}
			if len(q) != len(tc.wantFirst) {
				t.Errorf("query = %v, want exactly %v", q, tc.wantFirst)
			}
			for k, want := range tc.wantFirst {
				if got := q.Get(k); got != want {
					t.Errorf("query %s = %q, want %q", k, got, want)
				}
			}
			// The single-request read never carries a query string.
			if recs[1].RawQuery != "" {
				t.Errorf("getRequest query = %q, want empty", recs[1].RawQuery)
			}
			// The first log page omits sinceRow.
			for _, rec := range recs {
				if strings.HasSuffix(rec.Path, "/logs") && strings.Contains(rec.RawQuery, "sinceRow=0") {
					t.Error("sent sinceRow=0; an unset sinceRow is omitted")
				}
			}
		})
	}
}

func TestDiagnoseErrors(t *testing.T) {
	cases := []struct {
		name    string
		opts    mockapi.Options
		deps    map[string]mockapi.Deployment
		req     vcfdiag.DiagnoseRequest
		wantErr error
	}{
		{
			name:    "empty deployment id",
			deps:    seed(),
			req:     vcfdiag.DiagnoseRequest{},
			wantErr: vcfdiag.ErrInvalidRequest,
		},
		{
			name:    "path traversal in deployment id",
			deps:    seed(),
			req:     vcfdiag.DiagnoseRequest{DeploymentID: "a/b"},
			wantErr: vcfdiag.ErrInvalidRequest,
		},
		{
			name:    "negative page size",
			deps:    seed(),
			req:     vcfdiag.DiagnoseRequest{DeploymentID: "dep-1", PageSize: -3},
			wantErr: vcfdiag.ErrInvalidRequest,
		},
		{
			name: "no failed request",
			deps: map[string]mockapi.Deployment{"dep-1": {Requests: []mockapi.Request{
				{ID: "r-ok", Status: "SUCCESSFUL"},
			}}},
			req:     vcfdiag.DiagnoseRequest{DeploymentID: "dep-1"},
			wantErr: vcfdiag.ErrNoFailedRequest,
		},
		{
			name: "failed request with no error line",
			deps: map[string]mockapi.Deployment{"dep-1": {Requests: []mockapi.Request{
				{ID: "r-bad", Status: "FAILED", Events: []mockapi.Event{
					{ID: "e-1", Timestamp: "t", HasLogs: true, Logs: []string{"INFO fine", "WARN odd"}},
				}},
			}}},
			req:     vcfdiag.DiagnoseRequest{DeploymentID: "dep-1"},
			wantErr: vcfdiag.ErrNoRootCause,
		},
		{
			name: "failed request with no events at all",
			deps: map[string]mockapi.Deployment{"dep-1": {Requests: []mockapi.Request{
				{ID: "r-bad", Status: "FAILED"},
			}}},
			req:     vcfdiag.DiagnoseRequest{DeploymentID: "dep-1"},
			wantErr: vcfdiag.ErrNoRootCause,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			opts := tc.opts
			opts.Deployments = tc.deps
			opts.LogPageSize = 2
			srv := start(t, opts)
			c, err := vcfdiag.New(vcfdiag.Config{BaseURL: srv.URL(), Token: testToken})
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			_, err = c.Diagnose(context.Background(), tc.req)
			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("err = %v, want %v", err, tc.wantErr)
			}
		})
	}
}

func TestDiagnoseAPIErrors(t *testing.T) {
	cases := []struct {
		name   string
		opts   mockapi.Options
		wantOp string
	}{
		{"listing", mockapi.Options{RequestsStatus: http.StatusInternalServerError}, "getDeploymentRequests"},
		{"request", mockapi.Options{RequestStatus: http.StatusNotFound}, "getRequest"},
		{"events", mockapi.Options{EventsStatus: http.StatusForbidden}, "getRequestEvents"},
		{"logs", mockapi.Options{LogsStatus: http.StatusBadGateway}, "getEventLogs"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			opts := tc.opts
			opts.Deployments = seed()
			opts.LogPageSize = 2
			srv := start(t, opts)
			c, err := vcfdiag.New(vcfdiag.Config{BaseURL: srv.URL(), Token: testToken})
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			_, err = c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{DeploymentID: "dep-1"})

			var apiErr *vcfdiag.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("err = %v, want *APIError", err)
			}
			if apiErr.Op != tc.wantOp {
				t.Errorf("Op = %q, want %q", apiErr.Op, tc.wantOp)
			}
			if !strings.Contains(apiErr.Error(), tc.wantOp) {
				t.Errorf("Error() = %q, want it to name %q", apiErr.Error(), tc.wantOp)
			}
		})
	}
}
