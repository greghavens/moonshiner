package verify

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"

	"example.com/vcfdiag"
	"example.com/vcfdiag/mockapi"
)

const token = "tkn-verify-9f2c"

func contractPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(repoRoot(t), "docs", "contract.json")
}

// ---------------------------------------------------------------------------
// the fixture
//
// A failed deployment whose request record says nothing useful. The reason is
// in the logs of the first event, on the second page of those logs. Anything
// that skips the logs, stops at the first page, or takes the last error line
// instead of the first, lands on the rollback message — which is fallout, not
// the cause.
// ---------------------------------------------------------------------------

const (
	deploymentID = "dep-emea-checkout"
	failedReqID  = "req-2"

	rootCause = "ERROR Allocation failed: network profile prod-emea-nsx has 1 free address, 4 requested"
	cascade   = "ERROR Rollback did not complete: resource checkout-app-vm left in ERROR state"
)

func fixture() map[string]mockapi.Deployment {
	return map[string]mockapi.Deployment{
		deploymentID: {Requests: []mockapi.Request{
			// Newest first, as the API's default createdAt,DESC ordering gives.
			{
				ID: "req-3", Name: "Power On", Status: "SUCCESSFUL",
				Details: "Completed.", CreatedAt: "2026-03-04T11:20:00Z", UpdatedAt: "2026-03-04T11:21:10Z",
			},
			{
				ID: failedReqID, Name: "Update", Status: "FAILED",
				Details: "Request failed.", CreatedAt: "2026-03-04T09:02:00Z", UpdatedAt: "2026-03-04T09:07:41Z",
				Events: []mockapi.Event{
					{
						ID: "ev-1", Name: "Allocate network", Details: "Network allocation",
						Timestamp: "2026-03-04T09:02:30Z", ResourceName: "checkout-app-net",
						ResourceType: "Cloud.NSX.Network", HasLogs: true,
						Logs: []string{
							"INFO Starting network allocation for checkout-app-net",
							"INFO Selecting network profile prod-emea-nsx",
							"INFO Requesting 4 addresses from range 10.42.7.0/26",
							rootCause,
							"INFO Marking allocation task failed",
						},
					},
					{
						ID: "ev-2", Name: "Change approved", Details: "Approved by operator",
						Timestamp: "2026-03-04T09:04:00Z", HasLogs: false, UserEvent: true,
					},
					{
						ID: "ev-3", Name: "Rollback", Details: "Rolling back",
						Timestamp: "2026-03-04T09:06:00Z", ResourceName: "checkout-app-vm",
						ResourceType: "Cloud.vSphere.Machine", HasLogs: true,
						Logs: []string{
							"INFO Rolling back partially provisioned resources",
							cascade,
						},
					},
				},
			},
			{
				ID: "req-1", Name: "Create", Status: "FAILED",
				Details: "An older failure.", CreatedAt: "2026-03-01T08:00:00Z", UpdatedAt: "2026-03-01T08:09:00Z",
				Events: []mockapi.Event{{
					ID: "ev-0", Name: "Create", Timestamp: "2026-03-01T08:01:00Z", HasLogs: true,
					Logs: []string{"ERROR An older failure that is not the one being diagnosed"},
				}},
			},
		}},
	}
}

func startMock(t *testing.T, opts mockapi.Options) *mockapi.Server {
	t.Helper()
	if opts.ContractPath == "" {
		opts.ContractPath = contractPath(t)
	}
	if opts.Token == "" {
		opts.Token = token
	}
	srv, err := mockapi.Start(opts)
	if err != nil {
		t.Fatalf("mockapi.Start: %v", err)
	}
	t.Cleanup(srv.Close)
	if !strings.HasPrefix(srv.URL(), "http://127.0.0.1:") {
		t.Fatalf("mock listens on %q, want a 127.0.0.1 loopback address", srv.URL())
	}
	return srv
}

func newClient(t *testing.T, base string) *vcfdiag.Client {
	t.Helper()
	c, err := vcfdiag.New(vcfdiag.Config{BaseURL: base, Token: token})
	if err != nil {
		t.Fatalf("vcfdiag.New: %v", err)
	}
	return c
}

// ---------------------------------------------------------------------------
// the diagnosis
// ---------------------------------------------------------------------------

func TestDiagnoseFindsTheRootCauseInThePagedLogs(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})
	c := newClient(t, srv.URL())

	got, err := c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{
		DeploymentID: deploymentID,
		PageSize:     50,
	})
	if err != nil {
		t.Fatalf("Diagnose: %v", err)
	}

	checks := []struct{ field, got, want string }{
		{"DeploymentID", got.DeploymentID, deploymentID},
		{"RequestID", got.RequestID, failedReqID},
		{"RequestName", got.RequestName, "Update"},
		{"RequestStatus", got.RequestStatus, "FAILED"},
		{"RequestDetails", got.RequestDetails, "Request failed."},
		{"EventID", got.EventID, "ev-1"},
		{"EventName", got.EventName, "Allocate network"},
		{"ResourceName", got.ResourceName, "checkout-app-net"},
		{"RootCause", got.RootCause, rootCause},
	}
	for _, ch := range checks {
		if ch.got != ch.want {
			t.Errorf("%s = %q, want %q", ch.field, ch.got, ch.want)
		}
	}
	if got.RootCause == cascade {
		t.Error("RootCause is the rollback line: that is fallout from the failure, not its cause. " +
			"The cause is the first error line across the events, which is on the second page of ev-1's logs.")
	}

	// Every line of every event that has logs, in retrieval order, and nothing
	// from the event that has none.
	wantLines := []struct {
		event  string
		rownum int
		msg    string
	}{
		{"ev-1", 1, "INFO Starting network allocation for checkout-app-net"},
		{"ev-1", 2, "INFO Selecting network profile prod-emea-nsx"},
		{"ev-1", 3, "INFO Requesting 4 addresses from range 10.42.7.0/26"},
		{"ev-1", 4, rootCause},
		{"ev-1", 5, "INFO Marking allocation task failed"},
		{"ev-3", 1, "INFO Rolling back partially provisioned resources"},
		{"ev-3", 2, cascade},
	}
	if len(got.LogLines) != len(wantLines) {
		t.Fatalf("retrieved %d log lines, want %d (every line of ev-1 and ev-3, none from ev-2): %+v",
			len(got.LogLines), len(wantLines), got.LogLines)
	}
	for i, want := range wantLines {
		line := got.LogLines[i]
		if line.EventID != want.event || line.Rownum != want.rownum || line.Message != want.msg {
			t.Errorf("LogLines[%d] = {%s %d %q}, want {%s %d %q}",
				i, line.EventID, line.Rownum, line.Message, want.event, want.rownum, want.msg)
		}
		if line.Timestamp == "" {
			t.Errorf("LogLines[%d].Timestamp is empty", i)
		}
	}
}

// ---------------------------------------------------------------------------
// the wire shape
// ---------------------------------------------------------------------------

type wantCall struct {
	method string
	path   string
	query  map[string]string // exact key set; a key absent here must not be sent
}

func TestDiagnoseProducesTheExactRequestSequence(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})
	c := newClient(t, srv.URL())

	if _, err := c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{
		DeploymentID: deploymentID,
		PageSize:     50,
	}); err != nil {
		t.Fatalf("Diagnose: %v", err)
	}

	want := []wantCall{
		{"GET", "/deployment/api/deployments/dep-emea-checkout/requests",
			map[string]string{"size": "50", "sort": "createdAt,DESC"}},
		{"GET", "/deployment/api/requests/req-2", map[string]string{}},
		{"GET", "/deployment/api/requests/req-2/events",
			map[string]string{"size": "50", "sort": "timestamp,ASC"}},
		// First page of ev-1: sinceRow is unset, so it is not sent at all.
		{"GET", "/deployment/api/requests/req-2/events/ev-1/logs", map[string]string{}},
		// Second page: rows 1..3 were served, so the next row wanted is 4.
		{"GET", "/deployment/api/requests/req-2/events/ev-1/logs", map[string]string{"sinceRow": "4"}},
		// ev-2 has no logs and must not be asked for. ev-3 fits in one page.
		{"GET", "/deployment/api/requests/req-2/events/ev-3/logs", map[string]string{}},
	}

	assertCalls(t, srv.Requests(), want)
}

func TestDiagnoseOmitsUnsetOptionalQueryParameters(t *testing.T) {
	cases := []struct {
		name       string
		req        vcfdiag.DiagnoseRequest
		wantFirst  map[string]string
		wantEvents map[string]string
	}{
		{
			name:       "nothing set: only the sort the client always sends",
			req:        vcfdiag.DiagnoseRequest{DeploymentID: deploymentID},
			wantFirst:  map[string]string{"sort": "createdAt,DESC"},
			wantEvents: map[string]string{"sort": "timestamp,ASC"},
		},
		{
			name:       "page size only",
			req:        vcfdiag.DiagnoseRequest{DeploymentID: deploymentID, PageSize: 20},
			wantFirst:  map[string]string{"size": "20", "sort": "createdAt,DESC"},
			wantEvents: map[string]string{"size": "20", "sort": "timestamp,ASC"},
		},
		{
			name: "search only, and it does not leak onto the event listing",
			req:  vcfdiag.DiagnoseRequest{DeploymentID: deploymentID, Search: "checkout"},
			wantFirst: map[string]string{
				"search": "checkout", "sort": "createdAt,DESC",
			},
			wantEvents: map[string]string{"sort": "timestamp,ASC"},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})
			c := newClient(t, srv.URL())

			if _, err := c.Diagnose(context.Background(), tc.req); err != nil {
				t.Fatalf("Diagnose: %v", err)
			}
			got := srv.Requests()
			if len(got) < 3 {
				t.Fatalf("recorded %d requests, want at least 3", len(got))
			}
			assertQuery(t, "request listing", got[0].RawQuery, tc.wantFirst)
			assertQuery(t, "event listing", got[2].RawQuery, tc.wantEvents)

			// getRequest takes no query parameters at all.
			if got[1].RawQuery != "" {
				t.Errorf("getRequest was called with query %q, want none", got[1].RawQuery)
			}
		})
	}
}

// An unset parameter is left out of the query string. It is never sent with an
// empty value, and never sent as a zero.
func TestNoRequestCarriesAnEmptyOrZeroValuedParameter(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})
	c := newClient(t, srv.URL())

	if _, err := c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{DeploymentID: deploymentID}); err != nil {
		t.Fatalf("Diagnose: %v", err)
	}

	for i, rec := range srv.Requests() {
		q, err := url.ParseQuery(rec.RawQuery)
		if err != nil {
			t.Errorf("request %d has an unparseable query %q: %v", i, rec.RawQuery, err)
			continue
		}
		for name, values := range q {
			for _, v := range values {
				if v == "" {
					t.Errorf("request %d (%s) sent %q with an empty value: an unset optional parameter "+
						"must be omitted from the query string, not sent empty", i, rec.Path, name)
				}
			}
			if name == "sinceRow" && values[0] == "0" {
				t.Errorf("request %d (%s) sent sinceRow=0: the first page of logs omits sinceRow entirely",
					i, rec.Path)
			}
			if name == "size" && values[0] == "0" {
				t.Errorf("request %d (%s) sent size=0: an unset page size is omitted", i, rec.Path)
			}
		}
	}
}

func TestEveryRequestCarriesBearerAndAccept(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})
	c := newClient(t, srv.URL())

	if _, err := c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{DeploymentID: deploymentID}); err != nil {
		t.Fatalf("Diagnose: %v", err)
	}
	recs := srv.Requests()
	if len(recs) == 0 {
		t.Fatal("no requests recorded")
	}
	for i, rec := range recs {
		if got := rec.Header.Get("Authorization"); got != "Bearer "+token {
			t.Errorf("request %d Authorization = %q, want %q", i, got, "Bearer "+token)
		}
		if got := rec.Header.Get("Accept"); !strings.Contains(got, "application/json") {
			t.Errorf("request %d Accept = %q, want it to include application/json", i, got)
		}
		if len(rec.Body) != 0 {
			t.Errorf("request %d (%s) carried a body of %d bytes; every operation in the chain is a GET",
				i, rec.Path, len(rec.Body))
		}
	}
}

// ---------------------------------------------------------------------------
// the request log is a faithful record, not a story the mock tells
// ---------------------------------------------------------------------------

func TestRequestLogRecordsWhatWasActuallySent(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})

	// Probe the server directly, so the log can be checked against calls whose
	// exact shape is known here rather than produced by the client.
	probes := []struct {
		method, target, body string
	}{
		{"GET", "/deployment/api/requests/req-2", ""},
		{"POST", "/deployment/api/requests/req-2", `{"probe":true}`},
		{"GET", "/nothing/here?a=1&b=&c=0", ""},
		{"GET", "/deployment/api/requests/req-2/events/ev-1/logs?sinceRow=2", ""},
	}
	for _, p := range probes {
		req, err := http.NewRequest(p.method, srv.URL()+p.target, strings.NewReader(p.body))
		if err != nil {
			t.Fatalf("build probe: %v", err)
		}
		req.Header.Set("Authorization", "Bearer "+token)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("probe %s %s: %v", p.method, p.target, err)
		}
		resp.Body.Close()
	}

	recs := srv.Requests()
	if len(recs) != len(probes) {
		t.Fatalf("recorded %d requests, want %d: every request the server receives is recorded, in order",
			len(recs), len(probes))
	}
	for i, p := range probes {
		path, rawQuery, _ := strings.Cut(p.target, "?")
		if recs[i].Method != p.method || recs[i].Path != path || recs[i].RawQuery != rawQuery {
			t.Errorf("record %d = {%s %s ?%s}, want {%s %s ?%s}",
				i, recs[i].Method, recs[i].Path, recs[i].RawQuery, p.method, path, rawQuery)
		}
		if string(recs[i].Body) != p.body {
			t.Errorf("record %d body = %q, want %q", i, recs[i].Body, p.body)
		}
	}

	// The log the caller gets back is a copy: writing to it changes nothing.
	first := srv.Requests()
	first[0].Method = "TAMPERED"
	first[0].Path = "/tampered"
	first[1].Body[0] = 'X'
	if len(first[0].Header) > 0 {
		first[0].Header.Set("Authorization", "tampered")
	}
	again := srv.Requests()
	if again[0].Method != probes[0].method || again[0].Path != "/deployment/api/requests/req-2" {
		t.Error("Requests() handed back the server's own slice: a caller reading the log rewrote it")
	}
	if got := again[0].Header.Get("Authorization"); got != "Bearer "+token {
		t.Errorf("Requests() shares its header map with the server: Authorization is now %q", got)
	}
	if got := string(again[1].Body); got != probes[1].body {
		t.Errorf("Requests() shares its body bytes with the server: body is now %q", got)
	}
}

func TestMockRoutesOnlyTheContractOperations(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})

	cases := []struct {
		name       string
		method     string
		target     string
		auth       string
		wantStatus int
	}{
		{"unrouted path", "GET", "/deployment/api/deployments", "Bearer " + token, http.StatusNotFound},
		{"unrouted sibling", "GET", "/deployment/api/resources/r-1", "Bearer " + token, http.StatusNotFound},
		{"routed path, wrong method", "POST", "/deployment/api/requests/req-2", "Bearer " + token, http.StatusMethodNotAllowed},
		{"routed path, no token", "GET", "/deployment/api/requests/req-2", "", http.StatusUnauthorized},
		{"routed path, wrong token", "GET", "/deployment/api/requests/req-2", "Bearer nope", http.StatusUnauthorized},
		{"unknown deployment", "GET", "/deployment/api/deployments/dep-nope/requests", "Bearer " + token, http.StatusNotFound},
		{"unknown request", "GET", "/deployment/api/requests/req-nope", "Bearer " + token, http.StatusNotFound},
		{"unknown event", "GET", "/deployment/api/requests/req-2/events/ev-nope/logs", "Bearer " + token, http.StatusNotFound},
		{"event without logs", "GET", "/deployment/api/requests/req-2/events/ev-2/logs", "Bearer " + token, http.StatusNotFound},
		{"known request", "GET", "/deployment/api/requests/req-2", "Bearer " + token, http.StatusOK},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, srv.URL()+tc.target, nil)
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			if tc.auth != "" {
				req.Header.Set("Authorization", tc.auth)
			}
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("do: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != tc.wantStatus {
				t.Errorf("%s %s = %d, want %d", tc.method, tc.target, resp.StatusCode, tc.wantStatus)
			}
		})
	}
	if got := len(srv.Requests()); got != len(cases) {
		t.Errorf("recorded %d requests, want %d: unrouted, wrong-method and unauthorised requests must all be recorded", got, len(cases))
	}
}

func TestMockStartValidatesItsOptionsAndContract(t *testing.T) {
	good := contractPath(t)

	t.Run("empty contract path", func(t *testing.T) {
		if _, err := mockapi.Start(mockapi.Options{Token: token}); err == nil {
			t.Error("Start accepted an empty ContractPath")
		}
	})
	t.Run("empty token", func(t *testing.T) {
		if _, err := mockapi.Start(mockapi.Options{ContractPath: good}); err == nil {
			t.Error("Start accepted an empty Token")
		}
	})
	t.Run("missing contract", func(t *testing.T) {
		p := filepath.Join(t.TempDir(), "absent.json")
		if _, err := mockapi.Start(mockapi.Options{ContractPath: p, Token: token}); err == nil {
			t.Error("Start accepted a contract path that does not exist")
		}
	})
	t.Run("unparseable contract", func(t *testing.T) {
		p := filepath.Join(t.TempDir(), "contract.json")
		if err := os.WriteFile(p, []byte("{not json"), 0o644); err != nil {
			t.Fatal(err)
		}
		if _, err := mockapi.Start(mockapi.Options{ContractPath: p, Token: token}); err == nil {
			t.Error("Start accepted a contract that is not JSON")
		}
	})
	t.Run("no operations", func(t *testing.T) {
		p := writeContract(t, nil)
		if _, err := mockapi.Start(mockapi.Options{ContractPath: p, Token: token}); err == nil {
			t.Error("Start accepted a contract that names no operations")
		}
	})
	t.Run("operation the mock does not serve", func(t *testing.T) {
		p := writeContract(t, []map[string]any{
			{"id": "getDeploymentRequests", "method": "GET", "path": "/deployment/api/deployments/{deploymentId}/requests"},
			{"id": "getResourceActions", "method": "GET", "path": "/deployment/api/resources/{resourceId}/actions"},
		})
		_, err := mockapi.Start(mockapi.Options{ContractPath: p, Token: token})
		if err == nil {
			t.Fatal("Start accepted a contract naming an operation the mock does not serve")
		}
		if !strings.Contains(err.Error(), "getResourceActions") {
			t.Errorf("error %q does not name the unserved operation getResourceActions", err)
		}
	})
	t.Run("a contract naming fewer operations serves fewer routes", func(t *testing.T) {
		p := writeContract(t, []map[string]any{
			{"id": "getRequest", "method": "GET", "path": "/deployment/api/requests/{requestId}"},
		})
		srv, err := mockapi.Start(mockapi.Options{ContractPath: p, Token: token, Deployments: fixture()})
		if err != nil {
			t.Fatalf("Start: %v", err)
		}
		defer srv.Close()

		// The one named operation is routed.
		assertStatus(t, srv.URL()+"/deployment/api/requests/req-2", http.StatusOK)
		// The ones it does not name are not.
		assertStatus(t, srv.URL()+"/deployment/api/requests/req-2/events", http.StatusNotFound)
		assertStatus(t, srv.URL()+"/deployment/api/deployments/"+deploymentID+"/requests", http.StatusNotFound)
	})
}

// ---------------------------------------------------------------------------
// errors
// ---------------------------------------------------------------------------

func TestDiagnoseRejectsBadInputBeforeAnyRequest(t *testing.T) {
	cases := []struct {
		name string
		req  vcfdiag.DiagnoseRequest
	}{
		{"empty deployment id", vcfdiag.DiagnoseRequest{}},
		{"deployment id with a slash", vcfdiag.DiagnoseRequest{DeploymentID: "dep/../x"}},
		{"deployment id with a query marker", vcfdiag.DiagnoseRequest{DeploymentID: "dep?x=1"}},
		{"deployment id with a fragment marker", vcfdiag.DiagnoseRequest{DeploymentID: "dep#frag"}},
		{"negative page size", vcfdiag.DiagnoseRequest{DeploymentID: deploymentID, PageSize: -1}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})
			c := newClient(t, srv.URL())

			_, err := c.Diagnose(context.Background(), tc.req)
			if !errors.Is(err, vcfdiag.ErrInvalidRequest) {
				t.Fatalf("err = %v, want it to satisfy errors.Is(err, ErrInvalidRequest)", err)
			}
			if n := len(srv.Requests()); n != 0 {
				t.Errorf("%d requests were sent; a malformed DiagnoseRequest is rejected before any HTTP call", n)
			}
		})
	}
}

func TestNewRejectsBadConfig(t *testing.T) {
	cases := []struct {
		name string
		cfg  vcfdiag.Config
	}{
		{"empty base url", vcfdiag.Config{Token: token}},
		{"relative base url", vcfdiag.Config{BaseURL: "127.0.0.1:8080", Token: token}},
		{"empty token", vcfdiag.Config{BaseURL: "https://vcfa.example.test"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := vcfdiag.New(tc.cfg); !errors.Is(err, vcfdiag.ErrInvalidRequest) {
				t.Errorf("err = %v, want it to satisfy errors.Is(err, ErrInvalidRequest)", err)
			}
		})
	}
	t.Run("trailing slash is trimmed", func(t *testing.T) {
		srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})
		c, err := vcfdiag.New(vcfdiag.Config{BaseURL: srv.URL() + "/", Token: token})
		if err != nil {
			t.Fatalf("New: %v", err)
		}
		if _, err := c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{DeploymentID: deploymentID}); err != nil {
			t.Fatalf("Diagnose: %v", err)
		}
		for i, rec := range srv.Requests() {
			if strings.Contains(rec.Path, "//") {
				t.Errorf("request %d path %q has a doubled slash: the trailing slash on BaseURL was not trimmed",
					i, rec.Path)
			}
		}
	})
}

func TestDiagnoseReportsWhenThereIsNothingToDiagnose(t *testing.T) {
	t.Run("no failed request", func(t *testing.T) {
		deps := map[string]mockapi.Deployment{deploymentID: {Requests: []mockapi.Request{
			{ID: "req-9", Name: "Power On", Status: "SUCCESSFUL", CreatedAt: "2026-03-04T11:20:00Z"},
		}}}
		srv := startMock(t, mockapi.Options{Deployments: deps, LogPageSize: 3})
		c := newClient(t, srv.URL())

		_, err := c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{DeploymentID: deploymentID})
		if !errors.Is(err, vcfdiag.ErrNoFailedRequest) {
			t.Fatalf("err = %v, want it to satisfy errors.Is(err, ErrNoFailedRequest)", err)
		}
		// It looked, and it stopped after looking.
		if n := len(srv.Requests()); n != 1 {
			t.Errorf("sent %d requests, want 1: with no failed request there is nothing further to fetch", n)
		}
	})

	t.Run("logs carry no error line", func(t *testing.T) {
		deps := map[string]mockapi.Deployment{deploymentID: {Requests: []mockapi.Request{
			{ID: "req-5", Name: "Update", Status: "FAILED", Details: "Request failed.",
				CreatedAt: "2026-03-04T09:02:00Z",
				Events: []mockapi.Event{{
					ID: "ev-a", Name: "Allocate", Timestamp: "2026-03-04T09:02:30Z", HasLogs: true,
					Logs: []string{"INFO started", "WARN slow", "INFO done"},
				}},
			},
		}}}
		srv := startMock(t, mockapi.Options{Deployments: deps, LogPageSize: 2})
		c := newClient(t, srv.URL())

		_, err := c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{DeploymentID: deploymentID})
		if !errors.Is(err, vcfdiag.ErrNoRootCause) {
			t.Fatalf("err = %v, want it to satisfy errors.Is(err, ErrNoRootCause)", err)
		}
	})
}

func TestDiagnoseSurfacesAPIErrorsWithTheOperationThatFailed(t *testing.T) {
	cases := []struct {
		name   string
		opts   mockapi.Options
		wantOp string
	}{
		{"request listing fails", mockapi.Options{RequestsStatus: http.StatusInternalServerError}, "getDeploymentRequests"},
		{"request fetch fails", mockapi.Options{RequestStatus: http.StatusBadGateway}, "getRequest"},
		{"event listing fails", mockapi.Options{EventsStatus: http.StatusServiceUnavailable}, "getRequestEvents"},
		{"log fetch fails", mockapi.Options{LogsStatus: http.StatusInternalServerError}, "getEventLogs"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			opts := tc.opts
			opts.Deployments = fixture()
			opts.LogPageSize = 3
			srv := startMock(t, opts)
			c := newClient(t, srv.URL())

			_, err := c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{DeploymentID: deploymentID})
			var apiErr *vcfdiag.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("err = %v, want it to satisfy errors.As(err, **APIError)", err)
			}
			if apiErr.Op != tc.wantOp {
				t.Errorf("APIError.Op = %q, want %q", apiErr.Op, tc.wantOp)
			}
			if apiErr.StatusCode == 0 {
				t.Error("APIError.StatusCode is zero")
			}
		})
	}

	t.Run("wrong token", func(t *testing.T) {
		srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})
		c, err := vcfdiag.New(vcfdiag.Config{BaseURL: srv.URL(), Token: "not-the-token"})
		if err != nil {
			t.Fatalf("New: %v", err)
		}
		_, err = c.Diagnose(context.Background(), vcfdiag.DiagnoseRequest{DeploymentID: deploymentID})
		var apiErr *vcfdiag.APIError
		if !errors.As(err, &apiErr) {
			t.Fatalf("err = %v, want an *APIError", err)
		}
		if apiErr.StatusCode != http.StatusUnauthorized {
			t.Errorf("StatusCode = %d, want %d", apiErr.StatusCode, http.StatusUnauthorized)
		}
	})
}

func TestDiagnoseHonoursContextCancellation(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})
	c := newClient(t, srv.URL())

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if _, err := c.Diagnose(ctx, vcfdiag.DiagnoseRequest{DeploymentID: deploymentID}); err == nil {
		t.Error("Diagnose with a cancelled context returned no error")
	}
}

func TestClientIsSafeForConcurrentUse(t *testing.T) {
	srv := startMock(t, mockapi.Options{Deployments: fixture(), LogPageSize: 3})
	c := newClient(t, srv.URL())

	const n = 8
	var wg sync.WaitGroup
	results := make([]*vcfdiag.Diagnosis, n)
	errs := make([]error, n)

	wg.Add(n)
	for i := range n {
		go func() {
			defer wg.Done()
			results[i], errs[i] = c.Diagnose(context.Background(),
				vcfdiag.DiagnoseRequest{DeploymentID: deploymentID, PageSize: 50})
		}()
	}
	// Reading the log while requests are in flight is allowed.
	go srv.Requests()
	wg.Wait()

	for i := range n {
		if errs[i] != nil {
			t.Fatalf("Diagnose %d: %v", i, errs[i])
		}
		if results[i].RootCause != rootCause {
			t.Errorf("Diagnose %d RootCause = %q, want %q", i, results[i].RootCause, rootCause)
		}
	}
	if got := len(srv.Requests()); got != n*6 {
		t.Errorf("recorded %d requests, want %d (six per diagnosis)", got, n*6)
	}
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func assertCalls(t *testing.T, got []mockapi.Recorded, want []wantCall) {
	t.Helper()
	if len(got) != len(want) {
		t.Errorf("the client made %d requests, want %d", len(got), len(want))
		for i, rec := range got {
			t.Logf("  got[%d] %s %s ?%s", i, rec.Method, rec.Path, rec.RawQuery)
		}
		for i, w := range want {
			t.Logf("  want[%d] %s %s %v", i, w.method, w.path, w.query)
		}
		return
	}
	for i, w := range want {
		if got[i].Method != w.method {
			t.Errorf("request %d method = %q, want %q", i, got[i].Method, w.method)
		}
		if got[i].Path != w.path {
			t.Errorf("request %d path = %q, want %q", i, got[i].Path, w.path)
		}
		assertQuery(t, "request "+itoa(i)+" ("+w.path+")", got[i].RawQuery, w.query)
	}
}

// assertQuery pins the exact key set, not just the keys that are present: a
// parameter missing from want must not have been sent at all.
func assertQuery(t *testing.T, what, rawQuery string, want map[string]string) {
	t.Helper()
	parsed, err := url.ParseQuery(rawQuery)
	if err != nil {
		t.Errorf("%s: query %q does not parse: %v", what, rawQuery, err)
		return
	}
	got := map[string]string{}
	for k, v := range parsed {
		if len(v) != 1 {
			t.Errorf("%s: parameter %q sent %d times, want once", what, k, len(v))
		}
		got[k] = v[0]
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s: query = %v (raw %q), want exactly %v", what, got, rawQuery, want)
	}
}

func assertStatus(t *testing.T, target string, want int) {
	t.Helper()
	req, err := http.NewRequest("GET", target, nil)
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("get %s: %v", target, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != want {
		t.Errorf("GET %s = %d, want %d", target, resp.StatusCode, want)
	}
}

// writeContract writes a contract naming exactly ops, for testing how the mock
// pins itself to what the contract says.
func writeContract(t *testing.T, ops []map[string]any) string {
	t.Helper()
	doc := map[string]any{
		"api": map[string]any{
			"name": "test", "version": "9.1",
			"sourceType": "reference-documentation", "specificationAvailable": false,
		},
		"auth":       map[string]any{"scheme": "bearer", "header": "Authorization", "valuePrefix": "Bearer "},
		"operations": ops,
	}
	if ops == nil {
		doc["operations"] = []map[string]any{}
	}
	raw, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(t.TempDir(), "contract.json")
	if err := os.WriteFile(p, raw, 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}
