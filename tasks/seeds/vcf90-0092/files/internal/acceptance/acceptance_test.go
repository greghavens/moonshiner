package acceptance_test

import (
	"context"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"

	"example.com/vcflogs/logsapi"
	"example.com/vcflogs/mocklogs"
)

func ptr[T any](v T) *T { return &v }

func TestPinnedOfficialContract(t *testing.T) {
	t.Parallel()

	var sources struct {
		Repository string `json:"repository"`
		License    string `json:"license"`
		Tag        string `json:"tag"`
		CommitSHA  string `json:"commitSha"`
		SpecPath   string `json:"specPath"`
		SourceURL  string `json:"sourceUrl"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
	}
	readJSON(t, "../../docs/official_sources.json", &sources)
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" || sources.License != "Apache-2.0" {
		t.Fatalf("unexpected official repository or license: %+v", sources)
	}
	if sources.Tag != "9.0.0.0" || sources.CommitSHA != "85151f6b1bb58f13b6ac0304bfec53904bea085f" {
		t.Fatalf("official source is not pinned to the VCF 9.0 tag commit: %+v", sources)
	}
	if sources.SpecPath != "specifications/vcf-operations/vcf-operations-for-logs-openapi.json" {
		t.Fatalf("wrong specification path: %q", sources.SpecPath)
	}
	if !strings.Contains(sources.SourceURL, sources.CommitSHA+"/"+sources.SpecPath) {
		t.Fatalf("source URL is not commit-pinned: %q", sources.SourceURL)
	}
	wantSources := []struct{ OperationID, Method, Path string }{
		{"POST_sessions", "POST", "/sessions"},
		{"GET_events-+path", "GET", "/events/{+path}"},
	}
	if len(sources.Operations) != len(wantSources) {
		t.Fatalf("official source operation count = %d, want %d", len(sources.Operations), len(wantSources))
	}
	for i, want := range wantSources {
		got := sources.Operations[i]
		if got.OperationID != want.OperationID || got.Method != want.Method || got.Path != want.Path {
			t.Errorf("official operation %d = %+v, want %+v", i, got, want)
		}
	}

	var contract struct {
		Title          string `json:"title"`
		Version        string `json:"version"`
		ServerBasePath string `json:"serverBasePath"`
		Operations     map[string]struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
			Parameters  []struct {
				Name     string `json:"name"`
				Required bool   `json:"required"`
			} `json:"parameters"`
			Responses map[string]json.RawMessage `json:"responses"`
		} `json:"operations"`
	}
	readJSON(t, "../../docs/contract.json", &contract)
	if contract.Title != "VCF Operations for Logs" || contract.Version != "v2" || contract.ServerBasePath != "/api/v2" {
		t.Fatalf("unexpected contract identity: %+v", contract)
	}
	if len(contract.Operations) != 2 {
		t.Fatalf("contract exposes %d operations, want exactly 2", len(contract.Operations))
	}
	if got := contract.Operations[mocklogs.OperationPostSessions]; got.OperationID != mocklogs.OperationPostSessions || got.Method != "POST" || got.Path != "/sessions" {
		t.Fatalf("POST_sessions contract mismatch: %+v", got)
	}
	events := contract.Operations[mocklogs.OperationGetEventsPath]
	if events.OperationID != mocklogs.OperationGetEventsPath || events.Method != "GET" || events.Path != "/events/{+path}" {
		t.Fatalf("GET_events-+path contract mismatch: %+v", events)
	}
	wantParams := []string{"+path", "limit", "timeout", "view", "content-pack-fields", "order-by-direction"}
	if len(events.Parameters) != len(wantParams) {
		t.Fatalf("event parameter count = %d, want %d", len(events.Parameters), len(wantParams))
	}
	for i, name := range wantParams {
		if events.Parameters[i].Name != name || events.Parameters[i].Required != (i == 0) {
			t.Errorf("event parameter %d = %+v, want name %q required %v", i, events.Parameters[i], name, i == 0)
		}
	}
	if _, ok := events.Responses["440"]; !ok {
		t.Error("GET_events-+path contract does not record the expired-session 440 response")
	}
}

func TestQueryEventsRefreshesExpiredSessionWithoutLosingWork(t *testing.T) {
	t.Parallel()

	server := mocklogs.New([]mocklogs.Step{
		{OperationID: mocklogs.OperationPostSessions, StatusCode: http.StatusOK, Body: logsapi.SessionResponse{UserID: "11111111-1111-1111-1111-111111111111", SessionID: "token-one", TTL: 1800}},
		{OperationID: mocklogs.OperationGetEventsPath, StatusCode: http.StatusOK, Body: logsapi.EventsResponse{Complete: true, Events: []logsapi.Event{{Text: "boot complete", Timestamp: 1000}}}},
		{OperationID: mocklogs.OperationGetEventsPath, StatusCode: 440, Body: "Login Timeout"},
		{OperationID: mocklogs.OperationPostSessions, StatusCode: http.StatusOK, Body: logsapi.SessionResponse{UserID: "11111111-1111-1111-1111-111111111111", SessionID: "token-two", TTL: 1800}},
		{OperationID: mocklogs.OperationGetEventsPath, StatusCode: http.StatusOK, Body: logsapi.EventsResponse{Complete: true, Events: []logsapi.Event{{Text: "disk warning", Timestamp: 2000}}}},
		{OperationID: mocklogs.OperationGetEventsPath, StatusCode: http.StatusOK, Body: logsapi.EventsResponse{Complete: true, Results: []map[string]any{{"text": "service ready", "timestamp": float64(3000)}}}},
	})
	defer server.Close()

	client, err := logsapi.NewClient(server.URL(), logsapi.Credentials{Username: "admin", Password: "secret", Provider: "Local"}, server.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	queries := []logsapi.EventQuery{
		{Path: "text/CONTAINS boot"},
		{Path: "text/CONTAINS disk"},
		{
			Path:              "text/CONTAINS service ready",
			Limit:             ptr(25),
			Timeout:           ptr(1500),
			View:              ptr("SIMPLE"),
			ContentPackFields: []string{"pack-a", "pack two"},
			OrderByDirection:  ptr("ASC"),
		},
	}
	got, err := client.QueryEvents(context.Background(), queries)
	if err != nil {
		t.Fatalf("QueryEvents: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("response count = %d, want 3", len(got))
	}
	if got[0].Events[0].Text != "boot complete" || got[1].Events[0].Text != "disk warning" || got[2].Results[0]["text"] != "service ready" {
		t.Fatalf("responses were lost or reordered: %#v", got)
	}

	requests := server.Requests()
	want := []struct {
		method, requestURI, authorization, contentType, body string
	}{
		{"POST", "/api/v2/sessions", "", "application/json", `{"username":"admin","password":"secret","provider":"Local"}`},
		{"GET", "/api/v2/events/text/CONTAINS%20boot", "Bearer token-one", "", ""},
		{"GET", "/api/v2/events/text/CONTAINS%20disk", "Bearer token-one", "", ""},
		{"POST", "/api/v2/sessions", "", "application/json", `{"username":"admin","password":"secret","provider":"Local"}`},
		{"GET", "/api/v2/events/text/CONTAINS%20disk", "Bearer token-two", "", ""},
		{"GET", "/api/v2/events/text/CONTAINS%20service%20ready?content-pack-fields=pack-a&content-pack-fields=pack+two&limit=25&order-by-direction=ASC&timeout=1500&view=SIMPLE", "Bearer token-two", "", ""},
	}
	if len(requests) != len(want) {
		t.Fatalf("request count = %d, want %d: %#v", len(requests), len(want), requests)
	}
	for i := range want {
		got := requests[i]
		contentTypeMatches := want[i].contentType == "" || equivalentMediaType(got.Header.Get("Content-Type"), want[i].contentType)
		if got.Method != want[i].method || !equivalentRequestURI(got.RequestURI, want[i].requestURI) || got.Header.Get("Authorization") != want[i].authorization || !contentTypeMatches || !equivalentJSON(got.Body, want[i].body) {
			t.Errorf("request %d wire mismatch\n got: method=%q uri=%q auth=%q content-type=%q body=%q\nwant: method=%q uri=%q auth=%q content-type=%q body=%q", i, got.Method, got.RequestURI, got.Header.Get("Authorization"), got.Header.Get("Content-Type"), got.Body, want[i].method, want[i].requestURI, want[i].authorization, want[i].contentType, want[i].body)
		}
	}
	if strings.Contains(requests[1].RequestURI, "?") {
		t.Errorf("unset optional fields were serialized: %q", requests[1].RequestURI)
	}
}

func TestOptionalQueryWireShape(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name  string
		query logsapi.EventQuery
		want  string
	}{
		{
			name:  "all unset are omitted",
			query: logsapi.EventQuery{Path: "timestamp/LAST 60000"},
			want:  "/api/v2/events/timestamp/LAST%2060000",
		},
		{
			name: "explicit values and repeated content packs",
			query: logsapi.EventQuery{
				Path:              "text/CONTAINS a+b",
				Limit:             ptr(0),
				Timeout:           ptr(0),
				View:              ptr("DEFAULT"),
				ContentPackFields: []string{"alpha", "beta"},
				OrderByDirection:  ptr("DESC"),
			},
			want: "/api/v2/events/text/CONTAINS%20a+b?content-pack-fields=alpha&content-pack-fields=beta&limit=0&order-by-direction=DESC&timeout=0&view=DEFAULT",
		},
		{
			name:  "reserved characters are escaped within a segment",
			query: logsapi.EventQuery{Path: "text/CONTAINS a?b#c%"},
			want:  "/api/v2/events/text/CONTAINS%20a%3Fb%23c%25",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			server := mocklogs.New([]mocklogs.Step{
				{OperationID: mocklogs.OperationPostSessions, StatusCode: http.StatusOK, Body: logsapi.SessionResponse{UserID: "11111111-1111-1111-1111-111111111111", SessionID: "token", TTL: 1800}},
				{OperationID: mocklogs.OperationGetEventsPath, StatusCode: http.StatusOK, Body: logsapi.EventsResponse{Complete: true}},
			})
			defer server.Close()
			client, err := logsapi.NewClient(server.URL(), logsapi.Credentials{Username: "u", Password: "p", Provider: "Local"}, server.Client())
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			if _, err := client.QueryEvents(context.Background(), []logsapi.EventQuery{tt.query}); err != nil {
				t.Fatalf("QueryEvents: %v", err)
			}
			requests := server.Requests()
			if len(requests) != 2 {
				t.Fatalf("request count = %d, want 2", len(requests))
			}
			if !equivalentRequestURI(requests[1].RequestURI, tt.want) {
				t.Errorf("RequestURI = %q, want %q", requests[1].RequestURI, tt.want)
			}
		})
	}
}

func TestMockRejectsNonContractOperations(t *testing.T) {
	t.Parallel()

	server := mocklogs.New(nil)
	defer server.Close()

	tests := []struct {
		method string
		path   string
	}{
		{http.MethodGet, "/api/v2/sessions/current"},
		{http.MethodGet, "/api/v2/aggregated-events/text/EXISTS"},
		{http.MethodGet, "/api/v2/sessions"},
		{http.MethodPost, "/api/v2/events/text/EXISTS"},
	}
	for _, tt := range tests {
		req, err := http.NewRequest(tt.method, server.URL()+tt.path, nil)
		if err != nil {
			t.Fatalf("create %s %s: %v", tt.method, tt.path, err)
		}
		resp, err := server.Client().Do(req)
		if err != nil {
			t.Fatalf("%s %s: %v", tt.method, tt.path, err)
		}
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound {
			t.Errorf("%s %s status = %d, want 404", tt.method, tt.path, resp.StatusCode)
		}
	}
	if got := len(server.Requests()); got != len(tests) {
		t.Fatalf("request log length = %d, want %d", got, len(tests))
	}
}

func TestNonExpiryStatusIsReturnedAsError(t *testing.T) {
	t.Parallel()

	server := mocklogs.New([]mocklogs.Step{
		{OperationID: mocklogs.OperationPostSessions, StatusCode: http.StatusOK, Body: logsapi.SessionResponse{UserID: "11111111-1111-1111-1111-111111111111", SessionID: "token", TTL: 1800}},
		{OperationID: mocklogs.OperationGetEventsPath, StatusCode: http.StatusUnauthorized, Body: "Invalid session ID"},
	})
	defer server.Close()
	client, err := logsapi.NewClient(server.URL(), logsapi.Credentials{Username: "u", Password: "p", Provider: "Local"}, server.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	_, err = client.QueryEvents(context.Background(), []logsapi.EventQuery{{Path: "text/EXISTS"}})
	if err == nil {
		t.Fatal("QueryEvents returned nil error for a 401 response")
	}
	if got := len(server.Requests()); got != 2 {
		t.Fatalf("request count = %d, want 2 (401 must not be treated as expiry)", got)
	}
}

func TestAuthenticationFailureStopsBeforeQuery(t *testing.T) {
	t.Parallel()

	server := mocklogs.New([]mocklogs.Step{
		{OperationID: mocklogs.OperationPostSessions, StatusCode: http.StatusServiceUnavailable, Body: "not initially configured"},
	})
	defer server.Close()
	client, err := logsapi.NewClient(server.URL(), logsapi.Credentials{Username: "u", Password: "p", Provider: "Local"}, server.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if _, err := client.QueryEvents(context.Background(), []logsapi.EventQuery{{Path: "text/EXISTS"}}); err == nil {
		t.Fatal("QueryEvents returned nil error for a failed authentication request")
	}
	if got := len(server.Requests()); got != 1 {
		t.Fatalf("request count = %d, want 1 (no event query after failed authentication)", got)
	}
}

func TestNilHTTPClientUsesDefaultClient(t *testing.T) {
	t.Parallel()

	server := mocklogs.New([]mocklogs.Step{
		{OperationID: mocklogs.OperationPostSessions, StatusCode: http.StatusOK, Body: logsapi.SessionResponse{SessionID: "token"}},
		{OperationID: mocklogs.OperationGetEventsPath, StatusCode: http.StatusOK, Body: logsapi.EventsResponse{Complete: true}},
	})
	defer server.Close()
	client, err := logsapi.NewClient(server.URL(), logsapi.Credentials{Username: "u", Password: "p", Provider: "Local"}, nil)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if _, err := client.QueryEvents(context.Background(), []logsapi.EventQuery{{Path: "text/EXISTS"}}); err != nil {
		t.Fatalf("QueryEvents: %v", err)
	}
}

func TestMockRequestLogIsConcurrencySafe(t *testing.T) {
	t.Parallel()

	const requestCount = 64
	steps := make([]mocklogs.Step, requestCount)
	for i := range steps {
		steps[i] = mocklogs.Step{OperationID: mocklogs.OperationGetEventsPath, StatusCode: http.StatusOK, Body: logsapi.EventsResponse{Complete: true}}
	}
	server := mocklogs.New(steps)
	defer server.Close()

	start := make(chan struct{})
	errs := make(chan error, requestCount)
	var writers sync.WaitGroup
	for i := 0; i < requestCount; i++ {
		writers.Add(1)
		go func() {
			defer writers.Done()
			<-start
			resp, err := server.Client().Get(server.URL() + "/api/v2/events/text/EXISTS")
			if err == nil {
				_, _ = io.Copy(io.Discard, resp.Body)
				err = resp.Body.Close()
			}
			errs <- err
		}()
	}
	var readers sync.WaitGroup
	for i := 0; i < 8; i++ {
		readers.Add(1)
		go func() {
			defer readers.Done()
			<-start
			for j := 0; j < 1000; j++ {
				_ = server.Requests()
			}
		}()
	}
	close(start)
	writers.Wait()
	readers.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatalf("concurrent request: %v", err)
		}
	}
	if got := len(server.Requests()); got != requestCount {
		t.Fatalf("request log length = %d, want %d", got, requestCount)
	}
	snapshot := server.Requests()
	snapshot[0].Header.Set("X-Test-Mutation", "outside")
	if got := server.Requests()[0].Header.Get("X-Test-Mutation"); got != "" {
		t.Fatalf("request snapshot aliases the server log: X-Test-Mutation = %q", got)
	}
}

func readJSON(t *testing.T, path string, dst any) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(b, dst); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func equivalentRequestURI(got, want string) bool {
	gotURL, gotErr := url.ParseRequestURI(got)
	wantURL, wantErr := url.ParseRequestURI(want)
	if gotErr != nil || wantErr != nil {
		return false
	}
	gotSegments := strings.Split(gotURL.EscapedPath(), "/")
	wantSegments := strings.Split(wantURL.EscapedPath(), "/")
	if len(gotSegments) != len(wantSegments) {
		return false
	}
	for i := range gotSegments {
		gotSegment, gotErr := url.PathUnescape(gotSegments[i])
		wantSegment, wantErr := url.PathUnescape(wantSegments[i])
		if gotErr != nil || wantErr != nil || gotSegment != wantSegment {
			return false
		}
	}
	if gotURL.ForceQuery != wantURL.ForceQuery {
		return false
	}
	gotQuery, gotErr := url.ParseQuery(gotURL.RawQuery)
	wantQuery, wantErr := url.ParseQuery(wantURL.RawQuery)
	return gotErr == nil && wantErr == nil && equivalentQuery(gotQuery, wantQuery)
}

func equivalentQuery(got, want url.Values) bool {
	if len(got) != len(want) {
		return false
	}
	for key, gotValues := range got {
		wantValues, ok := want[key]
		if !ok || len(gotValues) != len(wantValues) {
			return false
		}
		gotCopy := append([]string(nil), gotValues...)
		wantCopy := append([]string(nil), wantValues...)
		sort.Strings(gotCopy)
		sort.Strings(wantCopy)
		if !reflect.DeepEqual(gotCopy, wantCopy) {
			return false
		}
	}
	return true
}

func equivalentMediaType(got, want string) bool {
	if got == "" || want == "" {
		return got == want
	}
	gotType, _, gotErr := mime.ParseMediaType(got)
	wantType, _, wantErr := mime.ParseMediaType(want)
	return gotErr == nil && wantErr == nil && gotType == wantType
}

func equivalentJSON(got, want string) bool {
	if got == "" || want == "" {
		return got == want
	}
	var gotValue, wantValue any
	if json.Unmarshal([]byte(got), &gotValue) != nil || json.Unmarshal([]byte(want), &wantValue) != nil {
		return false
	}
	return reflect.DeepEqual(gotValue, wantValue)
}

func TestExportedResponseJSONShape(t *testing.T) {
	t.Parallel()

	want := logsapi.EventsResponse{
		Complete: true,
		Duration: 12.5,
		Events: []logsapi.Event{{
			Text:            "message",
			Timestamp:       123,
			TimestampString: "time",
			Fields: []logsapi.EventField{{
				Name:          "appname",
				StartPosition: ptr(2),
				Length:        ptr(3),
			}},
		}},
		Warnings: []logsapi.Warning{{ID: 128, Details: "partial", Progress: ptr(0.5)}},
	}
	b, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	var got logsapi.EventsResponse
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("response JSON round trip mismatch\n got: %#v\nwant: %#v", got, want)
	}
}
