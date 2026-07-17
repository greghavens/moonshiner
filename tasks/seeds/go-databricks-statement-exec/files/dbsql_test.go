// Acceptance tests for the dbsql package (Databricks SQL Statement Execution
// API 2.0). A loopback fake workspace serves the wire contract pinned in
// docs/contract.json; a second loopback host plays the presigned-link store
// and records whether credentials leak to it. No real Databricks, no real
// credentials, no wall-clock sleeps — waiting is injected and recorded.
// Protected — do not modify this file or anything under docs/.
package dbsql

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	fixtureToken = "dapi7fixture20d1cc94ab58e3f7dummy" // dummy; must never leak
	warehouseID  = "abcdef0123456789"
	pollInterval = 2 * time.Second
)

type recorded struct {
	Method  string
	Path    string
	Auth    string
	HasAuth bool
	Accept  string
	CT      string
	Body    map[string]any
}

type fakeAPI struct {
	mu    sync.Mutex
	reqs  []recorded
	serve func(n int, r recorded) (int, string)
}

func (f *fakeAPI) handler(w http.ResponseWriter, r *http.Request) {
	var body map[string]any
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&body)
	}
	auth := r.Header.Get("Authorization")
	rec := recorded{
		Method:  r.Method,
		Path:    r.URL.Path,
		Auth:    auth,
		HasAuth: auth != "",
		Accept:  r.Header.Get("Accept"),
		CT:      r.Header.Get("Content-Type"),
		Body:    body,
	}
	f.mu.Lock()
	f.reqs = append(f.reqs, rec)
	n := len(f.reqs) - 1
	f.mu.Unlock()
	status, resp := f.serve(n, rec)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(resp))
}

func (f *fakeAPI) snapshot() []recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]recorded, len(f.reqs))
	copy(out, f.reqs)
	return out
}

type sleepLog struct {
	mu     sync.Mutex
	slept  []time.Duration
	onCall func(n int)
}

func (s *sleepLog) sleep(d time.Duration) {
	s.mu.Lock()
	s.slept = append(s.slept, d)
	n := len(s.slept)
	cb := s.onCall
	s.mu.Unlock()
	if cb != nil {
		cb(n)
	}
}

func (s *sleepLog) all() []time.Duration {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]time.Duration, len(s.slept))
	copy(out, s.slept)
	return out
}

func newClient(t *testing.T, base string, sl *sleepLog) *Client {
	t.Helper()
	c, err := New(Config{
		BaseURL:      base,
		Token:        fixtureToken,
		Sleep:        sl.sleep,
		PollInterval: pollInterval,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return c
}

func baseExec() ExecRequest {
	return ExecRequest{
		Statement:     "SELECT id, name, amount FROM billing.lines WHERE region = :region AND qty > :min_qty",
		WarehouseID:   warehouseID,
		Catalog:       "main",
		Schema:        "ops",
		Parameters:    []Param{{Name: "region", Value: "west", Type: "STRING"}, {Name: "min_qty", Value: "4", Type: "INT"}},
		WaitTimeout:   "10s",
		OnWaitTimeout: "CONTINUE",
		Disposition:   "INLINE",
		Format:        "JSON_ARRAY",
	}
}

const inlineSucceeded = `{
  "statement_id": "stmt-inline-00",
  "status": {"state": "SUCCEEDED"},
  "manifest": {
    "format": "JSON_ARRAY",
    "schema": {"column_count": 3, "columns": [
      {"name": "id", "type_name": "BIGINT", "type_text": "BIGINT", "position": 0},
      {"name": "name", "type_name": "STRING", "type_text": "STRING", "position": 1},
      {"name": "amount", "type_name": "DECIMAL", "type_text": "DECIMAL(10,2)", "position": 2}]},
    "total_chunk_count": 1, "total_row_count": 3, "truncated": false,
    "chunks": [{"chunk_index": 0, "row_offset": 0, "row_count": 3}]
  },
  "result": {"chunk_index": 0, "row_offset": 0, "row_count": 3,
    "data_array": [["1", "alpha", "19.99"], ["2", null, "0.50"], ["3", "gamma", null]]}
}`

func TestConfigValidation(t *testing.T) {
	if _, err := New(Config{}); err == nil {
		t.Fatalf("New must reject a config without BaseURL/Token")
	}
}

func TestExecInlineSynchronous(t *testing.T) {
	fake := &fakeAPI{serve: func(n int, r recorded) (int, string) { return 200, inlineSucceeded }}
	srv := httptest.NewServer(http.HandlerFunc(fake.handler))
	defer srv.Close()
	sl := &sleepLog{}
	c := newClient(t, srv.URL, sl)

	res, err := c.Exec(context.Background(), baseExec())
	if err != nil {
		t.Fatalf("Exec: %v", err)
	}
	reqs := fake.snapshot()
	if len(reqs) != 1 {
		t.Fatalf("a statement that completes within wait_timeout needs exactly 1 request, saw %d", len(reqs))
	}
	r := reqs[0]
	if r.Method != "POST" || r.Path != "/api/2.0/sql/statements" {
		t.Errorf("submit must be POST /api/2.0/sql/statements, got %s %s", r.Method, r.Path)
	}
	if r.Auth != "Bearer "+fixtureToken {
		t.Errorf("API calls must carry Bearer auth, got %q", r.Auth)
	}
	if r.Accept != "application/json" {
		t.Errorf("Accept: application/json required, got %q", r.Accept)
	}
	if !strings.HasPrefix(r.CT, "application/json") {
		t.Errorf("Content-Type: application/json required on POST, got %q", r.CT)
	}
	for k, want := range map[string]any{
		"statement":       "SELECT id, name, amount FROM billing.lines WHERE region = :region AND qty > :min_qty",
		"warehouse_id":    warehouseID,
		"catalog":         "main",
		"schema":          "ops",
		"wait_timeout":    "10s",
		"on_wait_timeout": "CONTINUE",
		"disposition":     "INLINE",
		"format":          "JSON_ARRAY",
	} {
		if got := r.Body[k]; got != want {
			t.Errorf("request body %s = %v, want %v", k, got, want)
		}
	}
	wantParams := []any{
		map[string]any{"name": "region", "value": "west", "type": "STRING"},
		map[string]any{"name": "min_qty", "value": "4", "type": "INT"},
	}
	if !reflect.DeepEqual(r.Body["parameters"], wantParams) {
		t.Errorf("parameters must be [{name,value,type}] in order, got %v", r.Body["parameters"])
	}
	if res.StatementID != "stmt-inline-00" {
		t.Errorf("StatementID = %q", res.StatementID)
	}
	wantCols := []Column{
		{Name: "id", TypeName: "BIGINT", TypeText: "BIGINT", Position: 0},
		{Name: "name", TypeName: "STRING", TypeText: "STRING", Position: 1},
		{Name: "amount", TypeName: "DECIMAL", TypeText: "DECIMAL(10,2)", Position: 2},
	}
	if !reflect.DeepEqual(res.Columns, wantCols) {
		t.Errorf("manifest schema must be retained verbatim, got %+v", res.Columns)
	}
	if res.TotalRowCount != 3 || res.ChunkCount != 1 || res.Truncated {
		t.Errorf("manifest metadata wrong: rows=%d chunks=%d truncated=%v",
			res.TotalRowCount, res.ChunkCount, res.Truncated)
	}
	if len(res.Rows) != 3 {
		t.Fatalf("want 3 rows, got %d", len(res.Rows))
	}
	if res.Rows[0][0] == nil || *res.Rows[0][0] != "1" {
		t.Errorf("typed values stay string-encoded: Rows[0][0] = %v", res.Rows[0][0])
	}
	if res.Rows[0][2] == nil || *res.Rows[0][2] != "19.99" {
		t.Errorf("decimal string must be preserved exactly: %v", res.Rows[0][2])
	}
	if res.Rows[1][1] != nil {
		t.Errorf("SQL NULL must decode to nil, got %q", *res.Rows[1][1])
	}
	if res.Rows[2][2] != nil {
		t.Errorf("SQL NULL must decode to nil, got %q", *res.Rows[2][2])
	}
	if len(sl.all()) != 0 {
		t.Errorf("no polling means no sleeps, got %v", sl.all())
	}
}

func TestAsyncPollUntilSucceeded(t *testing.T) {
	fake := &fakeAPI{}
	fake.serve = func(n int, r recorded) (int, string) {
		switch n {
		case 0:
			return 200, `{"statement_id": "stmt-async-01", "status": {"state": "PENDING"}}`
		case 1:
			return 200, `{"statement_id": "stmt-async-01", "status": {"state": "RUNNING"}}`
		default:
			return 200, strings.Replace(inlineSucceeded, "stmt-inline-00", "stmt-async-01", 1)
		}
	}
	srv := httptest.NewServer(http.HandlerFunc(fake.handler))
	defer srv.Close()
	sl := &sleepLog{}
	c := newClient(t, srv.URL, sl)

	req := baseExec()
	req.WaitTimeout = "0s"
	res, err := c.Exec(context.Background(), req)
	if err != nil {
		t.Fatalf("Exec: %v", err)
	}
	reqs := fake.snapshot()
	if len(reqs) != 3 {
		t.Fatalf("PENDING/RUNNING/SUCCEEDED needs 1 POST + 2 polls, saw %d requests", len(reqs))
	}
	if got := reqs[0].Body["wait_timeout"]; got != "0s" {
		t.Errorf("async submit must send wait_timeout 0s, got %v", got)
	}
	for i, r := range reqs[1:] {
		if r.Method != "GET" || r.Path != "/api/2.0/sql/statements/stmt-async-01" {
			t.Errorf("poll %d must be GET /api/2.0/sql/statements/stmt-async-01, got %s %s",
				i+1, r.Method, r.Path)
		}
		if r.Auth != "Bearer "+fixtureToken {
			t.Errorf("poll %d missing Bearer auth", i+1)
		}
	}
	want := []time.Duration{pollInterval, pollInterval}
	if !reflect.DeepEqual(sl.all(), want) {
		t.Errorf("one PollInterval sleep before each poll: got %v, want %v", sl.all(), want)
	}
	if res.StatementID != "stmt-async-01" || len(res.Rows) != 3 {
		t.Errorf("async result must be assembled after polling: id=%q rows=%d",
			res.StatementID, len(res.Rows))
	}
}

type cdnStore struct {
	mu   sync.Mutex
	hits []recorded
}

func (c *cdnStore) handler(w http.ResponseWriter, r *http.Request) {
	auth := r.Header.Get("Authorization")
	c.mu.Lock()
	c.hits = append(c.hits, recorded{Method: r.Method, Path: r.URL.Path, Auth: auth, HasAuth: auth != ""})
	c.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	switch r.URL.Path {
	case "/c0":
		_, _ = w.Write([]byte(`[["0-a","0-b"],["1-a","1-b"]]`))
	case "/c1":
		_, _ = w.Write([]byte(`[["2-a","2-b"],["3-a","3-b"]]`))
	case "/c2":
		_, _ = w.Write([]byte(`[["4-a",null]]`))
	default:
		w.WriteHeader(404)
	}
}

func linksManifest() string {
	return `"manifest": {
    "format": "JSON_ARRAY",
    "schema": {"column_count": 2, "columns": [
      {"name": "k", "type_name": "STRING", "type_text": "STRING", "position": 0},
      {"name": "v", "type_name": "STRING", "type_text": "STRING", "position": 1}]},
    "total_chunk_count": 3, "total_row_count": 5, "truncated": false,
    "chunks": [
      {"chunk_index": 0, "row_offset": 0, "row_count": 2},
      {"chunk_index": 1, "row_offset": 2, "row_count": 2},
      {"chunk_index": 2, "row_offset": 4, "row_count": 1}]
  }`
}

func externalLink(cdn string, idx, offset, count int, next bool) string {
	link := fmt.Sprintf(`{"chunk_index": %d, "row_offset": %d, "row_count": %d,
      "external_link": "%s/c%d", "expiration": "2026-07-17T09:00:00Z"`,
		idx, offset, count, cdn, idx)
	if next {
		link += fmt.Sprintf(`, "next_chunk_index": %d, "next_chunk_internal_link": "/api/2.0/sql/statements/stmt-links-02/result/chunks/%d"`,
			idx+1, idx+1)
	}
	return link + "}"
}

func TestExternalLinksTraversal(t *testing.T) {
	cdn := &cdnStore{}
	cdnSrv := httptest.NewServer(http.HandlerFunc(cdn.handler))
	defer cdnSrv.Close()

	fake := &fakeAPI{}
	fake.serve = func(n int, r recorded) (int, string) {
		switch {
		case r.Method == "POST":
			return 200, fmt.Sprintf(`{"statement_id": "stmt-links-02", "status": {"state": "SUCCEEDED"}, %s,
  "result": {"external_links": [%s]}}`, linksManifest(), externalLink(cdnSrv.URL, 0, 0, 2, true))
		case strings.HasSuffix(r.Path, "/result/chunks/1"):
			return 200, fmt.Sprintf(`{"external_links": [%s]}`, externalLink(cdnSrv.URL, 1, 2, 2, true))
		case strings.HasSuffix(r.Path, "/result/chunks/2"):
			return 200, fmt.Sprintf(`{"external_links": [%s]}`, externalLink(cdnSrv.URL, 2, 4, 1, false))
		default:
			return 404, `{"error_code": "ENDPOINT_NOT_FOUND", "message": "no route"}`
		}
	}
	srv := httptest.NewServer(http.HandlerFunc(fake.handler))
	defer srv.Close()
	sl := &sleepLog{}
	c := newClient(t, srv.URL, sl)

	req := baseExec()
	req.Disposition = "EXTERNAL_LINKS"
	res, err := c.Exec(context.Background(), req)
	if err != nil {
		t.Fatalf("Exec: %v", err)
	}
	if res.ChunkCount != 3 || res.TotalRowCount != 5 {
		t.Errorf("manifest counts must be retained: chunks=%d rows=%d", res.ChunkCount, res.TotalRowCount)
	}
	if len(res.Rows) != 5 {
		t.Fatalf("all chunk rows must be assembled, got %d", len(res.Rows))
	}
	for i, want := range []string{"0-a", "1-a", "2-a", "3-a", "4-a"} {
		if res.Rows[i][0] == nil || *res.Rows[i][0] != want {
			t.Errorf("row %d out of order: got %v, want %q", i, res.Rows[i][0], want)
		}
	}
	if res.Rows[4][1] != nil {
		t.Errorf("NULL in an external chunk must stay nil")
	}
	apiReqs := fake.snapshot()
	if len(apiReqs) != 3 {
		t.Fatalf("1 POST + 2 internal chunk GETs expected, saw %d", len(apiReqs))
	}
	for _, r := range apiReqs[1:] {
		if r.Method != "GET" || !strings.HasPrefix(r.Path, "/api/2.0/sql/statements/stmt-links-02/result/chunks/") {
			t.Errorf("internal links must be followed as documented chunk paths, got %s %s", r.Method, r.Path)
		}
		if r.Auth != "Bearer "+fixtureToken {
			t.Errorf("internal chunk requests require Bearer auth")
		}
	}
	cdn.mu.Lock()
	hits := append([]recorded(nil), cdn.hits...)
	cdn.mu.Unlock()
	if len(hits) != 3 {
		t.Fatalf("each external_link must be downloaded exactly once, saw %d", len(hits))
	}
	for _, h := range hits {
		if h.HasAuth {
			t.Errorf("Authorization header was forwarded to the presigned host (%s) — credentials leaked", h.Path)
		}
	}
}

func TestExternalChunkContinuityViolation(t *testing.T) {
	cdn := &cdnStore{}
	cdnSrv := httptest.NewServer(http.HandlerFunc(cdn.handler))
	defer cdnSrv.Close()
	fake := &fakeAPI{}
	fake.serve = func(n int, r recorded) (int, string) {
		if r.Method == "POST" {
			return 200, fmt.Sprintf(`{"statement_id": "stmt-links-02", "status": {"state": "SUCCEEDED"}, %s,
  "result": {"external_links": [%s]}}`, linksManifest(), externalLink(cdnSrv.URL, 0, 0, 2, true))
		}
		// chunk 1 claims row_offset 3 — rows 2..2 would be skipped
		return 200, fmt.Sprintf(`{"external_links": [%s]}`, externalLink(cdnSrv.URL, 1, 3, 2, false))
	}
	srv := httptest.NewServer(http.HandlerFunc(fake.handler))
	defer srv.Close()
	c := newClient(t, srv.URL, &sleepLog{})

	req := baseExec()
	req.Disposition = "EXTERNAL_LINKS"
	res, err := c.Exec(context.Background(), req)
	if err == nil {
		t.Fatalf("a chunk whose row_offset breaks contiguity must fail, got result %+v", res)
	}
	if !strings.Contains(err.Error(), "chunk") {
		t.Errorf("continuity error should identify the offending chunk, got %q", err.Error())
	}
}

func TestStatementFailedIsTyped(t *testing.T) {
	fake := &fakeAPI{serve: func(n int, r recorded) (int, string) {
		return 200, `{"statement_id": "stmt-fail-03", "status": {"state": "FAILED",
  "error": {"error_code": "BAD_REQUEST", "message": "[TABLE_OR_VIEW_NOT_FOUND] The table or view billing.linez cannot be found."},
  "sql_state": "42P01"}}`
	}}
	srv := httptest.NewServer(http.HandlerFunc(fake.handler))
	defer srv.Close()
	c := newClient(t, srv.URL, &sleepLog{})

	_, err := c.Exec(context.Background(), baseExec())
	var serr *StatementError
	if !errors.As(err, &serr) {
		t.Fatalf("FAILED state must surface as *StatementError, got %T: %v", err, err)
	}
	if serr.StatementID != "stmt-fail-03" {
		t.Errorf("StatementID = %q", serr.StatementID)
	}
	if serr.ErrorCode != "BAD_REQUEST" || serr.SQLState != "42P01" {
		t.Errorf("error_code/sql_state must be preserved: %q %q", serr.ErrorCode, serr.SQLState)
	}
	if !strings.Contains(serr.Message, "TABLE_OR_VIEW_NOT_FOUND") {
		t.Errorf("server message must be preserved, got %q", serr.Message)
	}
	if strings.Contains(err.Error(), fixtureToken) {
		t.Errorf("token leaked into error text")
	}
}

func TestAPIErrorEnvelope(t *testing.T) {
	fake := &fakeAPI{serve: func(n int, r recorded) (int, string) {
		return 403, `{"error_code": "PERMISSION_DENIED", "message": "User does not have CAN_USE on warehouse abcdef0123456789."}`
	}}
	srv := httptest.NewServer(http.HandlerFunc(fake.handler))
	defer srv.Close()
	c := newClient(t, srv.URL, &sleepLog{})

	_, err := c.Exec(context.Background(), baseExec())
	var aerr *APIError
	if !errors.As(err, &aerr) {
		t.Fatalf("non-2xx must surface as *APIError, got %T: %v", err, err)
	}
	if aerr.StatusCode != 403 || aerr.ErrorCode != "PERMISSION_DENIED" {
		t.Errorf("status/error_code wrong: %d %q", aerr.StatusCode, aerr.ErrorCode)
	}
	if aerr.Message != "User does not have CAN_USE on warehouse abcdef0123456789." {
		t.Errorf("message must be decoded verbatim, got %q", aerr.Message)
	}
	if strings.Contains(err.Error(), fixtureToken) || strings.Contains(err.Error(), "Bearer ") {
		t.Errorf("credentials leaked into error text: %q", err.Error())
	}
}

func TestContextCancellationStopsPolling(t *testing.T) {
	fake := &fakeAPI{}
	fake.serve = func(n int, r recorded) (int, string) {
		if n == 0 {
			return 200, `{"statement_id": "stmt-async-01", "status": {"state": "PENDING"}}`
		}
		return 200, `{"statement_id": "stmt-async-01", "status": {"state": "RUNNING"}}`
	}
	srv := httptest.NewServer(http.HandlerFunc(fake.handler))
	defer srv.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sl := &sleepLog{onCall: func(n int) {
		if n == 3 {
			cancel()
		}
	}}
	c := newClient(t, srv.URL, sl)

	req := baseExec()
	req.WaitTimeout = "0s"
	_, err := c.Exec(ctx, req)
	if err == nil {
		t.Fatalf("cancellation must abort a stuck statement")
	}
	if !errors.Is(err, context.Canceled) {
		t.Errorf("error must wrap context.Canceled, got %v", err)
	}
	if n := len(fake.snapshot()); n > 5 {
		t.Errorf("polling must stop promptly after cancellation, saw %d requests", n)
	}
}

func TestProtectedDocsFixtures(t *testing.T) {
	raw, err := os.ReadFile("docs/contract.json")
	if err != nil {
		t.Fatalf("contract fixture missing: %v", err)
	}
	var contract map[string]any
	if err := json.Unmarshal(raw, &contract); err != nil {
		t.Fatalf("contract.json must parse: %v", err)
	}
	ops := contract["operations"].(map[string]any)
	if p := ops["execute"].(map[string]any)["path"]; p != "/api/2.0/sql/statements" {
		t.Errorf("pinned execute path changed: %v", p)
	}
	states := contract["statement_states"].(map[string]any)["enum"].([]any)
	if len(states) != 6 {
		t.Errorf("expected the 6 documented statement states, got %v", states)
	}
	raw, err = os.ReadFile("docs/official_sources.json")
	if err != nil {
		t.Fatalf("sources fixture missing: %v", err)
	}
	var sources map[string]any
	if err := json.Unmarshal(raw, &sources); err != nil {
		t.Fatalf("official_sources.json must parse: %v", err)
	}
	research := sources["research"].(map[string]any)
	if research["required"] != true {
		t.Errorf("research.required must be true")
	}
	srcs := research["official_sources"].([]any)
	if len(srcs) < 2 {
		t.Errorf("at least two official sources required, got %d", len(srcs))
	}
	for _, s := range srcs {
		u := s.(map[string]any)["url"].(string)
		if !strings.HasPrefix(u, "https://") || !strings.Contains(u, "databricks") {
			t.Errorf("source must be a first-party Databricks page: %s", u)
		}
	}
	if facts := sources["verified_facts"].([]any); len(facts) < 4 {
		t.Errorf("contract facts must be summarized, got %d", len(facts))
	}
}
