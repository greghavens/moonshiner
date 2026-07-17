// Acceptance harness for the sfresults package: a loopback fake Snowflake
// SQL API v2 endpoint implementing the partitioned-result subset pinned in
// docs/contract.json. No vendor network, no real credentials.
// Protected — do not modify.
package sfresults_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"

	sfr "go-snowflake-partition-results"
)

const (
	token     = "dummy-jwt-7c31fa90aa" // dummy; must never leak into errors
	tokenType = "KEYPAIR_JWT"
	userAgent = "sfresults/1.0"
	basePath  = "/api/v2/statements"
	handle    = "01b70000-0000-4000-8000-000000000abc"
)

const rowTypeJSON = `[
      {"name": "ID", "type": "FIXED", "length": 0, "precision": 38, "scale": 0, "nullable": false},
      {"name": "LABEL", "type": "TEXT", "length": 16777216, "precision": 0, "scale": 0, "nullable": true},
      {"name": "PRICE", "type": "FIXED", "length": 0, "precision": 12, "scale": 2, "nullable": false},
      {"name": "ACTIVE", "type": "BOOLEAN", "length": 0, "precision": 0, "scale": 0, "nullable": false},
      {"name": "SEEN_AT", "type": "TIMESTAMP_NTZ", "length": 0, "precision": 0, "scale": 9, "nullable": true}
    ]`

const partition0Data = `[
    ["1", "alpha", "19.99", "true", "1752710400.000000000"],
    ["2", null, "5.00", "false", "1752710461.123456789"],
    ["3", "gamma", "0.01", "true", null]
  ]`

func makeSubmitBody(numRows int, partitionInfoJSON string) string {
	return fmt.Sprintf(`{
  "code": "090001",
  "sqlState": "00000",
  "message": "Statement executed successfully.",
  "statementHandle": "%s",
  "createdOn": 1752724800000,
  "statementStatusUrl": "%s/%s",
  "resultSetMetaData": {
    "numRows": %d,
    "format": "jsonv2",
    "rowType": %s,
    "partitionInfo": %s
  },
  "data": %s
}`, handle, basePath, handle, numRows, rowTypeJSON, partitionInfoJSON, partition0Data)
}

var submitBody3Parts = makeSubmitBody(6, `[
      {"rowCount": 3, "uncompressedSize": 210},
      {"rowCount": 2, "uncompressedSize": 152, "compressedSize": 88},
      {"rowCount": 1, "uncompressedSize": 76, "compressedSize": 44}
    ]`)

var submitBody1Part = makeSubmitBody(3, `[{"rowCount": 3, "uncompressedSize": 210}]`)

const partition1Body = `{"data": [
  ["4", "delta", "100.00", "false", "1752711000.500000000"],
  ["5", "épsilon", "42.42", "true", "1752711111.999999999"]
]}`

const partition2Body = `{"data": [
  ["6", null, "7.77", "false", null]
]}`

const failure422Body = `{
  "code": "001003",
  "sqlState": "42000",
  "message": "SQL compilation error:\nsyntax error line 1 at position 7 unexpected 'FORM'.",
  "statementHandle": "` + handle + `",
  "statementStatusUrl": "` + basePath + `/` + handle + `"
}`

type recorded struct {
	method  string
	path    string
	query   url.Values
	headers http.Header
	body    []byte
}

type partResp struct {
	status int
	body   string
}

type fakeSnow struct {
	mu           sync.Mutex
	reqs         []recorded
	submitStatus int
	submitBody   string
	partitions   map[int]partResp
}

func (f *fakeSnow) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	f.mu.Lock()
	f.reqs = append(f.reqs, recorded{
		method:  r.Method,
		path:    r.URL.Path,
		query:   r.URL.Query(),
		headers: r.Header.Clone(),
		body:    body,
	})
	f.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	switch {
	case r.Method == http.MethodPost && r.URL.Path == basePath:
		w.WriteHeader(f.submitStatus)
		fmt.Fprint(w, f.submitBody)
	case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, basePath+"/"):
		idx, err := strconv.Atoi(r.URL.Query().Get("partition"))
		if err != nil {
			w.WriteHeader(http.StatusBadRequest)
			fmt.Fprint(w, `{"message": "missing partition parameter"}`)
			return
		}
		resp, ok := f.partitions[idx]
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			fmt.Fprint(w, `{"message": "Statement handle not found or its results have expired."}`)
			return
		}
		w.WriteHeader(resp.status)
		fmt.Fprint(w, resp.body)
	default:
		w.WriteHeader(http.StatusNotFound)
		fmt.Fprint(w, `{"message": "unknown endpoint"}`)
	}
}

func (f *fakeSnow) snapshot() []recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]recorded, len(f.reqs))
	copy(out, f.reqs)
	return out
}

func idSeq() func() string {
	n := 0
	return func() string {
		n++
		return fmt.Sprintf("00000000-0000-4000-8000-%012d", n)
	}
}

func newClient(t *testing.T, f *fakeSnow) (*sfr.Client, *httptest.Server) {
	t.Helper()
	srv := httptest.NewServer(f)
	t.Cleanup(srv.Close)
	c := sfr.NewClient(sfr.Config{
		BaseURL:      srv.URL,
		Token:        token,
		TokenType:    tokenType,
		UserAgent:    userAgent,
		HTTPClient:   srv.Client(),
		NewRequestID: idSeq(),
	})
	return c, srv
}

func checkHeaders(t *testing.T, r recorded, wantContentType bool) {
	t.Helper()
	if got := r.headers.Get("Authorization"); got != "Bearer "+token {
		t.Errorf("%s %s: Authorization = %q, want Bearer <token>", r.method, r.path, got)
	}
	if got := r.headers.Get("X-Snowflake-Authorization-Token-Type"); got != tokenType {
		t.Errorf("%s %s: token type header = %q, want %q", r.method, r.path, got, tokenType)
	}
	if got := r.headers.Get("Accept"); got != "application/json" {
		t.Errorf("%s %s: Accept = %q, want application/json", r.method, r.path, got)
	}
	if got := r.headers.Get("User-Agent"); got != userAgent {
		t.Errorf("%s %s: User-Agent = %q, want %q (never the Go default)", r.method, r.path, got, userAgent)
	}
	if wantContentType {
		if got := r.headers.Get("Content-Type"); got != "application/json" {
			t.Errorf("%s %s: Content-Type = %q, want application/json", r.method, r.path, got)
		}
	} else if got := r.headers.Get("Content-Type"); got != "" {
		t.Errorf("%s %s: GET must not carry Content-Type, got %q", r.method, r.path, got)
	}
}

func v(s string) sfr.Value  { return sfr.Value{Valid: true, Raw: s} }
func nullv() sfr.Value      { return sfr.Value{} }

func TestProtectedFixtures(t *testing.T) {
	raw, err := os.ReadFile("docs/official_sources.json")
	if err != nil {
		t.Fatalf("official_sources.json: %v", err)
	}
	var src struct {
		Research struct {
			Required        bool `json:"required"`
			OfficialSources []struct {
				URL     string `json:"url"`
				UsedFor string `json:"used_for"`
			} `json:"official_sources"`
		} `json:"research"`
		VerifiedFacts []string `json:"verified_facts"`
	}
	if err := json.Unmarshal(raw, &src); err != nil {
		t.Fatalf("official_sources.json does not parse: %v", err)
	}
	if !src.Research.Required {
		t.Error("wave-8 seeds must record research provenance")
	}
	if len(src.Research.OfficialSources) < 2 {
		t.Error("at least two official sources required")
	}
	for _, s := range src.Research.OfficialSources {
		if !strings.HasPrefix(s.URL, "https://docs.snowflake.com/") {
			t.Errorf("non-first-party source %q", s.URL)
		}
		if s.UsedFor == "" {
			t.Errorf("source %q lacks used_for", s.URL)
		}
	}
	if len(src.VerifiedFacts) < 4 {
		t.Error("contract facts must be summarized")
	}

	raw, err = os.ReadFile("docs/contract.json")
	if err != nil {
		t.Fatalf("contract.json: %v", err)
	}
	var contract map[string]any
	if err := json.Unmarshal(raw, &contract); err != nil {
		t.Fatalf("contract.json does not parse: %v", err)
	}
	if contract["base_path"] != basePath {
		t.Errorf("contract base_path = %v", contract["base_path"])
	}
	auth := contract["auth"].(map[string]any)
	if auth["token_type_header"] != "X-Snowflake-Authorization-Token-Type" {
		t.Errorf("contract token_type_header = %v", auth["token_type_header"])
	}
	results := contract["results"].(map[string]any)
	if results["format"] != "jsonv2" {
		t.Errorf("contract result format = %v", results["format"])
	}
}

func TestSubmitRequestShape(t *testing.T) {
	f := &fakeSnow{submitStatus: 200, submitBody: submitBody3Parts,
		partitions: map[int]partResp{1: {200, partition1Body}, 2: {200, partition2Body}}}
	c, _ := newClient(t, f)

	_, err := c.ReadAll(context.Background(), sfr.Statement{
		SQL:       "select id, label, price, active, seen_at from catalog.items order by id",
		Timeout:   120,
		Database:  "ANALYTICS",
		Schema:    "PUBLIC",
		Warehouse: "WH_EXPORT",
		Role:      "EXPORTER",
	})
	if err != nil {
		t.Fatalf("ReadAll: %v", err)
	}
	reqs := f.snapshot()
	if len(reqs) == 0 || reqs[0].method != "POST" {
		t.Fatalf("first request must be the submit POST, got %+v", reqs)
	}
	post := reqs[0]
	if post.path != basePath {
		t.Errorf("submit path = %q, want %q", post.path, basePath)
	}
	checkHeaders(t, post, true)
	rid := post.query.Get("requestId")
	if rid != "00000000-0000-4000-8000-000000000001" {
		t.Errorf("requestId = %q, want the first injected id", rid)
	}
	var body map[string]any
	if err := json.Unmarshal(post.body, &body); err != nil {
		t.Fatalf("submit body is not JSON: %v", err)
	}
	want := map[string]any{
		"statement": "select id, label, price, active, seen_at from catalog.items order by id",
		"timeout":   float64(120),
		"database":  "ANALYTICS",
		"schema":    "PUBLIC",
		"warehouse": "WH_EXPORT",
		"role":      "EXPORTER",
	}
	if len(body) != len(want) {
		t.Errorf("submit body has extra/missing fields: %v", body)
	}
	for k, wv := range want {
		if body[k] != wv {
			t.Errorf("submit body[%q] = %v, want %v", k, body[k], wv)
		}
	}
}

func TestOptionalFieldsOmitted(t *testing.T) {
	f := &fakeSnow{submitStatus: 200, submitBody: submitBody1Part, partitions: map[int]partResp{}}
	c, _ := newClient(t, f)

	if _, err := c.ReadAll(context.Background(), sfr.Statement{SQL: "select 1"}); err != nil {
		t.Fatalf("ReadAll: %v", err)
	}
	var body map[string]any
	if err := json.Unmarshal(f.snapshot()[0].body, &body); err != nil {
		t.Fatalf("submit body is not JSON: %v", err)
	}
	if len(body) != 1 || body["statement"] != "select 1" {
		t.Errorf("unset context fields must be absent, not empty/zero: %v", body)
	}
}

func TestSinglePartitionNeedsNoExtraFetch(t *testing.T) {
	f := &fakeSnow{submitStatus: 200, submitBody: submitBody1Part, partitions: map[int]partResp{}}
	c, _ := newClient(t, f)

	rs, err := c.ReadAll(context.Background(), sfr.Statement{SQL: "select 1"})
	if err != nil {
		t.Fatalf("ReadAll: %v", err)
	}
	if got := len(f.snapshot()); got != 1 {
		t.Errorf("inline data is partition 0; a single-partition result means exactly 1 request, saw %d", got)
	}
	if len(rs.Rows) != 3 {
		t.Errorf("rows = %d, want 3", len(rs.Rows))
	}
	if rs.NumRows != 3 {
		t.Errorf("NumRows = %d, want 3", rs.NumRows)
	}
}

func TestMultiPartitionReadPreservesOrderAndValues(t *testing.T) {
	f := &fakeSnow{submitStatus: 200, submitBody: submitBody3Parts,
		partitions: map[int]partResp{1: {200, partition1Body}, 2: {200, partition2Body}}}
	c, _ := newClient(t, f)

	rs, err := c.ReadAll(context.Background(), sfr.Statement{SQL: "select * from catalog.items order by id"})
	if err != nil {
		t.Fatalf("ReadAll: %v", err)
	}

	if rs.StatementHandle != handle {
		t.Errorf("StatementHandle = %q", rs.StatementHandle)
	}
	if rs.NumRows != 6 {
		t.Errorf("NumRows = %d, want 6", rs.NumRows)
	}
	wantCols := []sfr.Column{
		{Name: "ID", Type: "FIXED", Precision: 38, Scale: 0, Nullable: false},
		{Name: "LABEL", Type: "TEXT", Precision: 0, Scale: 0, Nullable: true},
		{Name: "PRICE", Type: "FIXED", Precision: 12, Scale: 2, Nullable: false},
		{Name: "ACTIVE", Type: "BOOLEAN", Precision: 0, Scale: 0, Nullable: false},
		{Name: "SEEN_AT", Type: "TIMESTAMP_NTZ", Precision: 0, Scale: 9, Nullable: true},
	}
	if len(rs.Columns) != len(wantCols) {
		t.Fatalf("Columns = %v", rs.Columns)
	}
	for i, wc := range wantCols {
		if rs.Columns[i] != wc {
			t.Errorf("Columns[%d] = %+v, want %+v", i, rs.Columns[i], wc)
		}
	}
	wantParts := []sfr.PartitionInfo{
		{RowCount: 3, UncompressedSize: 210},
		{RowCount: 2, UncompressedSize: 152, CompressedSize: 88},
		{RowCount: 1, UncompressedSize: 76, CompressedSize: 44},
	}
	if len(rs.Partitions) != 3 {
		t.Fatalf("Partitions = %v", rs.Partitions)
	}
	for i, wp := range wantParts {
		if rs.Partitions[i] != wp {
			t.Errorf("Partitions[%d] = %+v, want %+v", i, rs.Partitions[i], wp)
		}
	}

	wantRows := [][]sfr.Value{
		{v("1"), v("alpha"), v("19.99"), v("true"), v("1752710400.000000000")},
		{v("2"), nullv(), v("5.00"), v("false"), v("1752710461.123456789")},
		{v("3"), v("gamma"), v("0.01"), v("true"), nullv()},
		{v("4"), v("delta"), v("100.00"), v("false"), v("1752711000.500000000")},
		{v("5"), v("épsilon"), v("42.42"), v("true"), v("1752711111.999999999")},
		{v("6"), nullv(), v("7.77"), v("false"), nullv()},
	}
	if len(rs.Rows) != len(wantRows) {
		t.Fatalf("Rows = %d, want %d (all partitions concatenated in order)", len(rs.Rows), len(wantRows))
	}
	for i, wr := range wantRows {
		if len(rs.Rows[i]) != len(wr) {
			t.Fatalf("Rows[%d] width = %d", i, len(rs.Rows[i]))
		}
		for j, wvv := range wr {
			if rs.Rows[i][j] != wvv {
				t.Errorf("Rows[%d][%d] = %+v, want %+v (string-exact, null-aware)", i, j, rs.Rows[i][j], wvv)
			}
		}
	}

	var partGets []recorded
	for _, r := range f.snapshot() {
		if r.method == "GET" {
			partGets = append(partGets, r)
		}
	}
	if len(partGets) != 2 {
		t.Fatalf("want exactly 2 partition GETs (1 and 2, never 0), saw %d", len(partGets))
	}
	for i, r := range partGets {
		wantIdx := strconv.Itoa(i + 1)
		if got := r.query.Get("partition"); got != wantIdx {
			t.Errorf("partition GET #%d requested partition=%q, want %q (ascending order)", i, got, wantIdx)
		}
		if r.path != basePath+"/"+handle {
			t.Errorf("partition GET path = %q, want %q", r.path, basePath+"/"+handle)
		}
		checkHeaders(t, r, false)
	}
}

func TestPartitionRowCountMismatch(t *testing.T) {
	short := `{"data": [["4", "delta", "100.00", "false", "1752711000.500000000"]]}`
	f := &fakeSnow{submitStatus: 200, submitBody: submitBody3Parts,
		partitions: map[int]partResp{1: {200, short}, 2: {200, partition2Body}}}
	c, _ := newClient(t, f)

	_, err := c.ReadAll(context.Background(), sfr.Statement{SQL: "select 1"})
	if err == nil {
		t.Fatal("a partition whose row count disagrees with partitionInfo must fail")
	}
	if !strings.Contains(err.Error(), "partition 1") {
		t.Errorf("mismatch error must name the offending partition index: %v", err)
	}
}

func TestExpiredHandleStopsTheRead(t *testing.T) {
	f := &fakeSnow{submitStatus: 200, submitBody: submitBody3Parts,
		partitions: map[int]partResp{2: {200, partition2Body}}} // partition 1 -> 404
	c, _ := newClient(t, f)

	_, err := c.ReadAll(context.Background(), sfr.Statement{SQL: "select 1"})
	if err == nil {
		t.Fatal("an expired statement handle must fail the read")
	}
	var expired *sfr.HandleExpiredError
	if !errors.As(err, &expired) {
		t.Fatalf("want HandleExpiredError, got %T: %v", err, err)
	}
	if expired.StatementHandle != handle {
		t.Errorf("HandleExpiredError.StatementHandle = %q", expired.StatementHandle)
	}
	if expired.Partition != 1 {
		t.Errorf("HandleExpiredError.Partition = %d, want 1", expired.Partition)
	}
	gets := 0
	for _, r := range f.snapshot() {
		if r.method == "GET" {
			gets++
		}
	}
	if gets != 1 {
		t.Errorf("after a 404 the reader must stop: want exactly 1 GET, saw %d", gets)
	}
	if strings.Contains(err.Error(), token) {
		t.Error("bearer token leaked into the error text")
	}
}

func TestSubmitFailure422(t *testing.T) {
	f := &fakeSnow{submitStatus: 422, submitBody: failure422Body, partitions: map[int]partResp{}}
	c, _ := newClient(t, f)

	_, err := c.ReadAll(context.Background(), sfr.Statement{SQL: "select * form catalog.items"})
	if err == nil {
		t.Fatal("a 422 QueryFailureStatus must fail the read")
	}
	var apiErr *sfr.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("want APIError, got %T: %v", err, err)
	}
	if apiErr.Status != 422 {
		t.Errorf("Status = %d, want 422", apiErr.Status)
	}
	if apiErr.Code != "001003" || apiErr.SQLState != "42000" {
		t.Errorf("Code/SQLState = %q/%q, want 001003/42000", apiErr.Code, apiErr.SQLState)
	}
	if !strings.Contains(apiErr.Message, "SQL compilation error") {
		t.Errorf("Message = %q", apiErr.Message)
	}
	if apiErr.StatementHandle != handle {
		t.Errorf("StatementHandle = %q", apiErr.StatementHandle)
	}
	text := err.Error()
	if !strings.Contains(text, "001003") || !strings.Contains(text, "42000") {
		t.Errorf("error text must carry the Snowflake code and sqlState: %q", text)
	}
	if strings.Contains(text, token) {
		t.Error("bearer token leaked into the error text")
	}
	if len(f.snapshot()) != 1 {
		t.Errorf("a 422 is terminal: exactly 1 request, saw %d", len(f.snapshot()))
	}
}

func TestUnauthorizedNeverLeaksToken(t *testing.T) {
	f := &fakeSnow{submitStatus: 401,
		submitBody: `{"message": "Authorization token has expired."}`, partitions: map[int]partResp{}}
	c, _ := newClient(t, f)

	_, err := c.ReadAll(context.Background(), sfr.Statement{SQL: "select 1"})
	if err == nil {
		t.Fatal("a 401 must fail the read")
	}
	var apiErr *sfr.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("want APIError, got %T: %v", err, err)
	}
	if apiErr.Status != 401 {
		t.Errorf("Status = %d, want 401", apiErr.Status)
	}
	if strings.Contains(err.Error(), token) {
		t.Error("bearer token leaked into the error text")
	}
}
