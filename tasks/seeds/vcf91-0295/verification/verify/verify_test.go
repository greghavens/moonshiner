// Package verify holds the protected acceptance tests for the netinsight
// client. Every test drives the client against the loopback appliance in
// internal/nimock and then asserts the exact wire shape from that appliance's
// request log. No live VMware endpoint is contacted.
package verify

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"mime"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"vcfopsnetworks/internal/nimock"
	"vcfopsnetworks/netinsight"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"

	specPath  = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
	specSHA   = "c3f3b52c845dd967cabbc21680e893292077d5ba"
	basePath  = "/api/ni"
	authValue = "NetworkInsight {token}"

	testUser  = "admin@vrni.com"
	testPass  = "s3cret-passw0rd"
	testToken = "1rT7tm4riiACSfxrO2BvkA=="
	testReqID = "TASK_PROGRESS_application.APP_BULK_SAVE.1641371956491.0.007518507960020182"
)

var wantOperationIDs = []string{
	"create",
	"delete",
	"getBulkApplicationTaskProgress",
	"saveDiscoveredApplications",
}

func boolPtr(b bool) *bool { return &b }

// baseScenario is a two-application batch that finishes after three polls.
func baseScenario() nimock.Scenario {
	saved := []nimock.AppResult{
		{EntityID: "18203:565:2854896465419091802", Name: "support-app-web", ResponseCode: "SUCCESS"},
		{EntityID: "18203:565:3896568950496372144", Name: "email-app", ResponseCode: "ALREADY_SAVED_APPLICATION", ErrorMessage: "Application email-app is already saved."},
	}
	return nimock.Scenario{
		Username:  testUser,
		Password:  testPass,
		Token:     testToken,
		Expiry:    1605201960327,
		RequestID: testReqID,
		TaskName:  "APP_BULK_SAVE",
		StartTime: 1641321499579,
		Steps: []nimock.ProgressStep{
			{Status: "SCHEDULED", Progress: 0},
			{Status: "IN_PROGRESS", Progress: 50, Apps: saved[:1]},
			{Status: "FINISHED", Progress: 100, Apps: saved},
		},
	}
}

func startMock(t *testing.T, sc nimock.Scenario) *nimock.Server {
	t.Helper()
	srv, err := nimock.New(contractPath, sc)
	if err != nil {
		t.Fatalf("start loopback appliance: %v", err)
	}
	t.Cleanup(srv.Close)
	return srv
}

func newClient(t *testing.T, srv *nimock.Server) *netinsight.Client {
	t.Helper()
	c, err := netinsight.NewClient(srv.URL(), nil)
	if err != nil {
		t.Fatalf("NewClient(%q): %v", srv.URL(), err)
	}
	if c == nil {
		t.Fatal("NewClient returned a nil client and a nil error")
	}
	return c
}

func authedClient(t *testing.T, srv *nimock.Server) *netinsight.Client {
	t.Helper()
	c := newClient(t, srv)
	if _, err := c.CreateToken(context.Background(), netinsight.Credentials{Username: testUser, Password: testPass}); err != nil {
		t.Fatalf("CreateToken: %v", err)
	}
	return c
}

func decodeBody(t *testing.T, r nimock.Request) map[string]any {
	t.Helper()
	var got map[string]any
	if err := json.Unmarshal(r.Body, &got); err != nil {
		t.Fatalf("%s %s: body is not a JSON object: %v (raw %q)", r.Method, r.Path, err, string(r.Body))
	}
	return got
}

func assertJSONContentType(t *testing.T, r nimock.Request) {
	t.Helper()
	mt, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mt != "application/json" {
		t.Errorf("%s %s: Content-Type = %q, want application/json", r.Method, r.Path, r.Header.Get("Content-Type"))
	}
}

func assertBody(t *testing.T, r nimock.Request, want map[string]any) {
	t.Helper()
	got := decodeBody(t, r)
	if reflect.DeepEqual(got, want) {
		return
	}
	gotJSON, _ := json.Marshal(got)
	wantJSON, _ := json.Marshal(want)
	t.Errorf("%s %s request body mismatch\n got: %s\nwant: %s\nraw sent: %s",
		r.Method, r.Path, gotJSON, wantJSON, strings.TrimSpace(string(r.Body)))
	for k := range got {
		if _, ok := want[k]; !ok {
			t.Errorf("  unset optional field %q must be omitted from the request body, it was sent as %#v", k, got[k])
		}
	}
	for k, v := range want {
		if _, ok := got[k]; !ok {
			t.Errorf("  field %q is missing from the request body, want %#v", k, v)
		}
	}
}

func onlyRequest(t *testing.T, srv *nimock.Server, operationID string) nimock.Request {
	t.Helper()
	reqs := srv.RequestsFor(operationID)
	if len(reqs) != 1 {
		t.Fatalf("operation %s: got %d requests, want exactly 1", operationID, len(reqs))
	}
	return reqs[0]
}

// --- docs -------------------------------------------------------------------

func TestDocsArePinnedToTheSpecification(t *testing.T) {
	var contract struct {
		BasePath string `json:"base_path"`
		Source   struct {
			CommitSHA string `json:"commit_sha"`
			SpecPath  string `json:"spec_path"`
		} `json:"source"`
		Auth struct {
			Header      string `json:"header"`
			ValueFormat string `json:"value_format"`
		} `json:"auth"`
		Operations map[string]struct {
			Method        string `json:"method"`
			Path          string `json:"path"`
			Authenticated bool   `json:"authenticated"`
		} `json:"operations"`
	}
	readJSON(t, contractPath, &contract)

	var sources struct {
		Sources []struct {
			CommitSHA    string   `json:"commit_sha"`
			SpecPath     string   `json:"spec_path"`
			OperationIDs []string `json:"operation_ids"`
			Operations   []struct {
				OperationID string `json:"operation_id"`
				Method      string `json:"method"`
				Path        string `json:"path"`
				SpecPointer string `json:"spec_pointer"`
			} `json:"operations"`
		} `json:"sources"`
	}
	readJSON(t, sourcesPath, &sources)

	if len(sources.Sources) != 1 {
		t.Fatalf("official_sources.json: got %d sources, want 1", len(sources.Sources))
	}
	src := sources.Sources[0]

	for _, tc := range []struct{ name, got, want string }{
		{"contract source.spec_path", contract.Source.SpecPath, specPath},
		{"contract source.commit_sha", contract.Source.CommitSHA, specSHA},
		{"contract base_path", contract.BasePath, basePath},
		{"contract auth.header", contract.Auth.Header, "Authorization"},
		{"contract auth.value_format", contract.Auth.ValueFormat, authValue},
		{"official_sources spec_path", src.SpecPath, specPath},
		{"official_sources commit_sha", src.CommitSHA, specSHA},
	} {
		if tc.got != tc.want {
			t.Errorf("%s = %q, want %q", tc.name, tc.got, tc.want)
		}
	}

	gotIDs := append([]string(nil), src.OperationIDs...)
	sort.Strings(gotIDs)
	if !reflect.DeepEqual(gotIDs, wantOperationIDs) {
		t.Errorf("official_sources operation_ids = %v, want %v", gotIDs, wantOperationIDs)
	}

	var contractIDs []string
	for id := range contract.Operations {
		contractIDs = append(contractIDs, id)
	}
	sort.Strings(contractIDs)
	if !reflect.DeepEqual(contractIDs, wantOperationIDs) {
		t.Errorf("contract operations = %v, want %v", contractIDs, wantOperationIDs)
	}

	wantRoutes := map[string]string{
		"create":                         "POST /auth/token",
		"delete":                         "DELETE /auth/token",
		"saveDiscoveredApplications":     "POST /groups/discovered-applications/save",
		"getBulkApplicationTaskProgress": "GET /groups/task/progress/{requestId}",
	}
	for id, want := range wantRoutes {
		op, ok := contract.Operations[id]
		if !ok {
			t.Errorf("contract is missing operation %q", id)
			continue
		}
		if got := op.Method + " " + op.Path; got != want {
			t.Errorf("contract operation %q = %q, want %q", id, got, want)
		}
	}
	if contract.Operations["create"].Authenticated {
		t.Error(`contract operation "create" is marked authenticated; the specification gives it an empty security list`)
	}

	for _, op := range src.Operations {
		if !strings.HasPrefix(op.SpecPointer, "#/paths/") {
			t.Errorf("official_sources operation %q: spec_pointer %q does not point into #/paths", op.OperationID, op.SpecPointer)
		}
	}
}

func readJSON(t *testing.T, path string, into any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(raw, into); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
}

// --- the mock is pinned to the contract -------------------------------------

func TestLoopbackApplianceServesOnlyContractOperations(t *testing.T) {
	srv := startMock(t, baseScenario())

	cases := []struct {
		name   string
		method string
		path   string
		want   int
	}{
		{"contract operation", http.MethodPost, basePath + "/auth/token", http.StatusBadRequest},
		{"operation outside the contract", http.MethodGet, basePath + "/entities", http.StatusNotFound},
		{"another operation outside the contract", http.MethodGet, basePath + "/data-sources/bulk/view-details/abc", http.StatusNotFound},
		{"contract path, wrong method", http.MethodPut, basePath + "/auth/token", http.StatusMethodNotAllowed},
		{"outside the base path", http.MethodGet, "/auth/token", http.StatusNotFound},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, srv.URL()+tc.path, strings.NewReader("{}"))
			if err != nil {
				t.Fatal(err)
			}
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != tc.want {
				t.Errorf("%s %s = %d, want %d", tc.method, tc.path, resp.StatusCode, tc.want)
			}
		})
	}

	if got := len(srv.Requests()); got != len(cases) {
		t.Errorf("request log holds %d entries, want %d", got, len(cases))
	}
}

// --- create ------------------------------------------------------------------

func TestCreateTokenWireShape(t *testing.T) {
	cases := []struct {
		name string
		cred netinsight.Credentials
		want map[string]any
	}{
		{
			name: "no domain is omitted entirely",
			cred: netinsight.Credentials{Username: testUser, Password: testPass},
			want: map[string]any{"username": testUser, "password": testPass},
		},
		{
			name: "local domain omits the unset value",
			cred: netinsight.Credentials{Username: testUser, Password: testPass, Domain: &netinsight.Domain{Type: "LOCAL"}},
			want: map[string]any{
				"username": testUser, "password": testPass,
				"domain": map[string]any{"domain_type": "LOCAL"},
			},
		},
		{
			name: "ldap domain carries both fields",
			cred: netinsight.Credentials{Username: testUser, Password: testPass, Domain: &netinsight.Domain{Type: "LDAP", Value: "corp.example.com"}},
			want: map[string]any{
				"username": testUser, "password": testPass,
				"domain": map[string]any{"domain_type": "LDAP", "value": "corp.example.com"},
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, baseScenario())
			c := newClient(t, srv)

			tok, err := c.CreateToken(context.Background(), tc.cred)
			if err != nil {
				t.Fatalf("CreateToken: %v", err)
			}
			if tok.Value != testToken {
				t.Errorf("token = %q, want %q", tok.Value, testToken)
			}
			if tok.Expiry != 1605201960327 {
				t.Errorf("expiry = %d, want 1605201960327", tok.Expiry)
			}

			r := onlyRequest(t, srv, "create")
			if r.Method != http.MethodPost {
				t.Errorf("method = %s, want POST", r.Method)
			}
			if r.Path != basePath+"/auth/token" {
				t.Errorf("path = %q, want %q", r.Path, basePath+"/auth/token")
			}
			if r.RawQuery != "" {
				t.Errorf("query = %q, want none", r.RawQuery)
			}
			if got := r.Header.Get("Authorization"); got != "" {
				t.Errorf("Authorization = %q, want no header: the specification gives this operation an empty security list", got)
			}
			assertJSONContentType(t, r)
			assertBody(t, r, tc.want)
		})
	}
}

func TestCreateTokenSurfacesAPIError(t *testing.T) {
	srv := startMock(t, baseScenario())
	c := newClient(t, srv)

	_, err := c.CreateToken(context.Background(), netinsight.Credentials{Username: testUser, Password: "wrong"})
	if err == nil {
		t.Fatal("CreateToken with bad credentials returned no error")
	}
	var apiErr *netinsight.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error %v is not a *netinsight.APIError", err)
	}
	if apiErr.StatusCode != http.StatusUnauthorized {
		t.Errorf("StatusCode = %d, want 401", apiErr.StatusCode)
	}
	if apiErr.Message != "bad credentials" {
		t.Errorf("APIError.Message = %q, want the message from the ApiError body %q", apiErr.Message, "bad credentials")
	}
}

// --- save --------------------------------------------------------------------

func TestSaveDiscoveredApplicationsWireShape(t *testing.T) {
	ids := []string{"18203:565:2854896465419091802", "18203:565:3896568950496372144"}
	wantApps := []any{
		map[string]any{"source_entity_id": ids[0]},
		map[string]any{"source_entity_id": ids[1]},
	}

	cases := []struct {
		name string
		req  netinsight.SaveRequest
		want map[string]any
	}{
		{
			name: "both optional fields unset are omitted",
			req:  netinsight.SaveRequest{SourceEntityIDs: ids},
			want: map[string]any{"discovered_apps": wantApps},
		},
		{
			name: "an explicit false enable_intent still reaches the wire",
			req:  netinsight.SaveRequest{SourceEntityIDs: ids, EnableIntent: boolPtr(false)},
			want: map[string]any{"discovered_apps": wantApps, "enable_intent": false},
		},
		{
			name: "an explicit true enable_intent reaches the wire",
			req:  netinsight.SaveRequest{SourceEntityIDs: ids, EnableIntent: boolPtr(true)},
			want: map[string]any{"discovered_apps": wantApps, "enable_intent": true},
		},
		{
			name: "discovery type set, enable_intent unset",
			req:  netinsight.SaveRequest{SourceEntityIDs: ids, DiscoveryType: "FLOW_BASED_DISCOVERY"},
			want: map[string]any{"discovered_apps": wantApps, "discovery_type": "FLOW_BASED_DISCOVERY"},
		},
		{
			name: "every field set",
			req:  netinsight.SaveRequest{SourceEntityIDs: ids, DiscoveryType: "SERVICE_NOW", EnableIntent: boolPtr(false)},
			want: map[string]any{"discovered_apps": wantApps, "discovery_type": "SERVICE_NOW", "enable_intent": false},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, baseScenario())
			c := authedClient(t, srv)

			gotID, err := c.SaveDiscoveredApplications(context.Background(), tc.req)
			if err != nil {
				t.Fatalf("SaveDiscoveredApplications: %v", err)
			}
			if gotID != testReqID {
				t.Errorf("request id = %q, want %q", gotID, testReqID)
			}

			r := onlyRequest(t, srv, "saveDiscoveredApplications")
			if r.Method != http.MethodPost {
				t.Errorf("method = %s, want POST", r.Method)
			}
			if want := basePath + "/groups/discovered-applications/save"; r.Path != want {
				t.Errorf("path = %q, want %q", r.Path, want)
			}
			if got, want := r.Header.Get("Authorization"), "NetworkInsight "+testToken; got != want {
				t.Errorf("Authorization = %q, want %q", got, want)
			}
			assertJSONContentType(t, r)
			assertBody(t, r, tc.want)
		})
	}
}

func TestSaveDiscoveredApplicationsWithoutTokenIsUnauthorized(t *testing.T) {
	srv := startMock(t, baseScenario())
	c := newClient(t, srv)

	_, err := c.SaveDiscoveredApplications(context.Background(), netinsight.SaveRequest{SourceEntityIDs: []string{"a"}})
	if err == nil {
		t.Fatal("save without a token returned no error")
	}
	var apiErr *netinsight.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error %v is not a *netinsight.APIError", err)
	}
	if apiErr.StatusCode != http.StatusUnauthorized {
		t.Errorf("StatusCode = %d, want 401", apiErr.StatusCode)
	}
}

// --- polling -----------------------------------------------------------------

func TestSaveAndWaitPollsToTerminalState(t *testing.T) {
	sc := baseScenario()
	srv := startMock(t, sc)
	c := authedClient(t, srv)

	got, err := c.SaveAndWait(context.Background(),
		netinsight.SaveRequest{SourceEntityIDs: []string{"18203:565:2854896465419091802", "18203:565:3896568950496372144"}},
		time.Millisecond)
	if err != nil {
		t.Fatalf("SaveAndWait: %v", err)
	}

	want := netinsight.TaskProgress{
		RequestID: testReqID,
		TaskName:  "APP_BULK_SAVE",
		Status:    "FINISHED",
		Progress:  100,
		StartTime: 1641321499579,
		AppResults: []netinsight.AppSaveResult{
			{EntityID: "18203:565:2854896465419091802", Name: "support-app-web", ResponseCode: "SUCCESS"},
			{EntityID: "18203:565:3896568950496372144", Name: "email-app", ResponseCode: "ALREADY_SAVED_APPLICATION", ErrorMessage: "Application email-app is already saved."},
		},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("terminal progress mismatch\n got: %+v\nwant: %+v", got, want)
	}

	if n := len(srv.RequestsFor("saveDiscoveredApplications")); n != 1 {
		t.Errorf("submitted the batch %d times, want exactly 1", n)
	}
	polls := srv.RequestsFor("getBulkApplicationTaskProgress")
	if len(polls) != len(sc.Steps) {
		t.Fatalf("polled %d times, want %d: the task must be polled until it reports a terminal state, not assumed complete",
			len(polls), len(sc.Steps))
	}
	for i, r := range polls {
		if r.Method != http.MethodGet {
			t.Errorf("poll %d: method = %s, want GET", i, r.Method)
		}
		if want := basePath + "/groups/task/progress/" + testReqID; r.Path != want {
			t.Errorf("poll %d: path = %q, want %q", i, r.Path, want)
		}
		if len(r.Body) != 0 {
			t.Errorf("poll %d: sent a %d byte body, want none", i, len(r.Body))
		}
		if got, want := r.Header.Get("Authorization"), "NetworkInsight "+testToken; got != want {
			t.Errorf("poll %d: Authorization = %q, want %q", i, got, want)
		}
	}

	log := srv.Requests()
	saveAt, firstPollAt := -1, -1
	for i, r := range log {
		if r.OperationID == "saveDiscoveredApplications" && saveAt < 0 {
			saveAt = i
		}
		if r.OperationID == "getBulkApplicationTaskProgress" && firstPollAt < 0 {
			firstPollAt = i
		}
	}
	if saveAt < 0 || firstPollAt < 0 || saveAt > firstPollAt {
		t.Errorf("request log order is wrong: save at %d, first poll at %d", saveAt, firstPollAt)
	}
}

func TestSaveAndWaitStopsAtTerminalFailure(t *testing.T) {
	sc := baseScenario()
	sc.Steps = []nimock.ProgressStep{
		{Status: "IN_PROGRESS", Progress: 20},
		{Status: "FAILED", Progress: 60, Apps: []nimock.AppResult{
			{EntityID: "18203:565:2854896465419091802", Name: "support-app-web", ResponseCode: "INTERNAL_ERROR", ErrorMessage: "tier creation failed"},
		}},
		{Status: "FINISHED", Progress: 100},
	}
	srv := startMock(t, sc)
	c := authedClient(t, srv)

	got, err := c.SaveAndWait(context.Background(),
		netinsight.SaveRequest{SourceEntityIDs: []string{"18203:565:2854896465419091802"}}, time.Millisecond)
	if err == nil {
		t.Fatal("SaveAndWait returned no error for a task that ended in FAILED")
	}
	var failed *netinsight.TaskFailedError
	if !errors.As(err, &failed) {
		t.Fatalf("error %v is not a *netinsight.TaskFailedError", err)
	}
	if failed.Progress.Status != "FAILED" {
		t.Errorf("TaskFailedError.Progress.Status = %q, want FAILED", failed.Progress.Status)
	}
	if len(failed.Progress.AppResults) != 1 {
		t.Errorf("TaskFailedError carries %d app results, want the 1 from the failing report", len(failed.Progress.AppResults))
	}
	if !reflect.DeepEqual(got, failed.Progress) {
		t.Errorf("returned terminal report differs from TaskFailedError.Progress\n got: %+v\nwant: %+v", got, failed.Progress)
	}
	if n := len(srv.RequestsFor("getBulkApplicationTaskProgress")); n != 2 {
		t.Errorf("polled %d times, want 2: FAILED is terminal and polling must stop there", n)
	}
}

func TestSaveAndWaitStopsAtCancelled(t *testing.T) {
	sc := baseScenario()
	sc.Steps = []nimock.ProgressStep{
		{Status: "IN_PROGRESS", Progress: 20},
		{Status: "CANCELLED", Progress: 20},
		{Status: "FINISHED", Progress: 100},
	}
	srv := startMock(t, sc)
	c := authedClient(t, srv)

	got, err := c.SaveAndWait(context.Background(),
		netinsight.SaveRequest{SourceEntityIDs: []string{"18203:565:2854896465419091802"}}, time.Millisecond)
	if err == nil {
		t.Fatal("SaveAndWait returned no error for a task that ended in CANCELLED")
	}
	var failed *netinsight.TaskFailedError
	if !errors.As(err, &failed) {
		t.Fatalf("error %v is not a *netinsight.TaskFailedError", err)
	}
	if failed.Progress.Status != "CANCELLED" {
		t.Errorf("TaskFailedError.Progress.Status = %q, want CANCELLED", failed.Progress.Status)
	}
	if !reflect.DeepEqual(got, failed.Progress) {
		t.Errorf("returned terminal report differs from TaskFailedError.Progress\n got: %+v\nwant: %+v", got, failed.Progress)
	}
	if n := len(srv.RequestsFor("getBulkApplicationTaskProgress")); n != 2 {
		t.Errorf("polled %d times, want 2: CANCELLED is terminal and polling must stop there", n)
	}
}

func TestSaveAndWaitHonoursContextDeadline(t *testing.T) {
	sc := baseScenario()
	sc.Steps = []nimock.ProgressStep{{Status: "IN_PROGRESS", Progress: 10}}
	srv := startMock(t, sc)
	c := authedClient(t, srv)

	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()

	done := make(chan error, 1)
	go func() {
		_, err := c.SaveAndWait(ctx, netinsight.SaveRequest{SourceEntityIDs: []string{"a"}}, 5*time.Millisecond)
		done <- err
	}()

	select {
	case err := <-done:
		if err == nil {
			t.Fatal("SaveAndWait returned no error for a task that never reaches a terminal state")
		}
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Errorf("error %v does not wrap context.DeadlineExceeded", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("SaveAndWait did not return after its context expired")
	}
}

func TestGetTaskProgressUnknownRequestID(t *testing.T) {
	srv := startMock(t, baseScenario())
	c := authedClient(t, srv)

	_, err := c.GetTaskProgress(context.Background(), "TASK_PROGRESS_application.APP_BULK_SAVE.0.0")
	if err == nil {
		t.Fatal("GetTaskProgress for an unknown request id returned no error")
	}
	var apiErr *netinsight.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error %v is not a *netinsight.APIError", err)
	}
	if apiErr.StatusCode != http.StatusNotFound {
		t.Errorf("StatusCode = %d, want 404", apiErr.StatusCode)
	}
}

// --- delete ------------------------------------------------------------------

func TestDeleteTokenWireShape(t *testing.T) {
	srv := startMock(t, baseScenario())
	c := authedClient(t, srv)

	if err := c.DeleteToken(context.Background()); err != nil {
		t.Fatalf("DeleteToken: %v", err)
	}

	r := onlyRequest(t, srv, "delete")
	if r.Method != http.MethodDelete {
		t.Errorf("method = %s, want DELETE", r.Method)
	}
	if r.Path != basePath+"/auth/token" {
		t.Errorf("path = %q, want %q", r.Path, basePath+"/auth/token")
	}
	if got, want := r.Header.Get("Authorization"), "NetworkInsight "+testToken; got != want {
		t.Errorf("Authorization = %q, want %q", got, want)
	}
	if len(r.Body) != 0 {
		t.Errorf("sent a %d byte body, want none", len(r.Body))
	}

	_, err := c.SaveDiscoveredApplications(context.Background(), netinsight.SaveRequest{SourceEntityIDs: []string{"a"}})
	if err == nil {
		t.Fatal("a call after DeleteToken succeeded; the deleted token must not be reused")
	}
	saves := srv.RequestsFor("saveDiscoveredApplications")
	if len(saves) > 1 {
		t.Fatalf("operation saveDiscoveredApplications: got %d requests after one call, want at most 1", len(saves))
	}
	if len(saves) == 1 {
		if got, deleted := saves[0].Header.Get("Authorization"), "NetworkInsight "+testToken; got == deleted {
			t.Errorf("call after DeleteToken reused deleted Authorization value %q", got)
		}
	}
}

// --- concurrency -------------------------------------------------------------

func TestClientIsSafeForConcurrentUse(t *testing.T) {
	sc := baseScenario()
	sc.Steps = []nimock.ProgressStep{{Status: "FINISHED", Progress: 100}}
	srv := startMock(t, sc)
	c := authedClient(t, srv)

	const n = 8
	errs := make(chan error, n)
	for i := 0; i < n; i++ {
		go func(i int) {
			_, err := c.GetTaskProgress(context.Background(), testReqID)
			errs <- err
		}(i)
	}
	for i := 0; i < n; i++ {
		if err := <-errs; err != nil {
			t.Errorf("concurrent GetTaskProgress: %v", err)
		}
	}
	if got := len(srv.RequestsFor("getBulkApplicationTaskProgress")); got != n {
		t.Errorf("appliance saw %d polls, want %d", got, n)
	}
	fmt.Fprintln(os.Stderr, "verified against the loopback appliance only; no live VMware endpoint was contacted")
}
