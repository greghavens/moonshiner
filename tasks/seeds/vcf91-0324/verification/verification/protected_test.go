// Package verification is the acceptance suite for this task.
//
// It is protected: it is restored to its original contents before grading, so
// editing it has no effect. It performs no network I/O — every request it
// makes goes to a mock bound to 127.0.0.1.
package verification

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"vcfauto/automation"
	"vcfauto/contract"
	"vcfauto/mock"
	"vcfauto/wire"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"

	testTenant   = "acme-org"
	testAPIToken = "vcfa-api-token-fixture-0123456789"
)

// --- digest helpers ---------------------------------------------------------

func digest(s string) string {
	h := sha256.Sum256([]byte(digestSalt + s))
	return hex.EncodeToString(h[:])
}

// normalizePath rewrites every {placeholder} to {} so that the contract is
// free to name its path parameters however it likes.
func normalizePath(p string) string {
	var b strings.Builder
	depth := 0
	for _, r := range p {
		switch r {
		case '{':
			depth++
			if depth == 1 {
				b.WriteString("{}")
			}
		case '}':
			if depth > 0 {
				depth--
			}
		default:
			if depth == 0 {
				b.WriteRune(r)
			}
		}
	}
	return b.String()
}

// requireDigests fails unless every wanted digest is matched by some name.
func requireDigests(t *testing.T, what string, names []string, want []string) {
	t.Helper()
	have := map[string]bool{}
	for _, n := range names {
		have[digest(n)] = true
	}
	missing := 0
	for _, w := range want {
		if !have[w] {
			missing++
		}
	}
	if missing > 0 {
		sort.Strings(names)
		t.Errorf("%s: %d of the %d identifiers the reference documents are missing or misspelled.\n"+
			"declared: %s\n"+
			"(the expected names are pinned as digests; re-read the reference page for this operation)",
			what, missing, len(want), strings.Join(names, ", "))
	}
}

func semanticFields(fields []contract.Field) string {
	copyFields := append([]contract.Field(nil), fields...)
	sort.Slice(copyFields, func(i, j int) bool { return copyFields[i].Name < copyFields[j].Name })
	var b strings.Builder
	for _, f := range copyFields {
		fmt.Fprintf(&b, "%q,%q,%t,%q,%t;", f.Name, f.Type, f.Required, f.Default, f.Deprecated)
	}
	return b.String()
}

func semanticOperation(op *contract.Operation) string {
	request := "none"
	if op.RequestBody != nil {
		request = op.RequestBody.ContentType + "[" + semanticFields(op.RequestBody.Fields) + "]"
	}
	return fmt.Sprintf("id=%q|method=%q|path=%q|path_params=[%s]|query=[%s]|request=%s|response=%q,%q,[%s]",
		op.ID, op.Method, op.Path, semanticFields(op.PathParams), semanticFields(op.Query), request,
		op.Response.ContentType, op.Response.Kind, semanticFields(op.Response.Fields))
}

func loadContract(t *testing.T) *contract.Contract {
	t.Helper()
	c, err := contract.Load(contractPath)
	if err != nil {
		t.Fatalf("load %s: %v", contractPath, err)
	}
	return c
}

// expand fills an operation's path parameters positionally.
func expand(t *testing.T, op *contract.Operation, values ...string) string {
	t.Helper()
	names, err := contract.PathParamNames(op.Path)
	if err != nil {
		t.Fatalf("operation %s: %v", op.ID, err)
	}
	if len(names) != len(values) {
		t.Fatalf("operation %s: path takes %d parameters, got %d", op.ID, len(names), len(values))
	}
	params := map[string]string{}
	for i, n := range names {
		params[n] = values[i]
	}
	p, err := op.ExpandPath(params)
	if err != nil {
		t.Fatalf("operation %s: %v", op.ID, err)
	}
	return p
}

// --- contract and provenance ------------------------------------------------

func TestContractDeclaresItsSourceAsReferenceDocumentation(t *testing.T) {
	c := loadContract(t)

	if c.Source.Kind != contract.SourceKindReferenceDocumentation {
		t.Errorf("source.kind = %q, want %q", c.Source.Kind, contract.SourceKindReferenceDocumentation)
	}
	if c.Source.SpecificationAvailable {
		t.Error("source.specification_available = true, but VCF Automation publishes no specification")
	}
	lower := strings.ToLower(c.Source.Statement)
	for _, phrase := range []string{"reference documentation", "specification"} {
		if !strings.Contains(lower, phrase) {
			t.Errorf("source.statement does not mention %q; it must say plainly that this contract "+
				"derives from reference documentation rather than from a published specification.\n"+
				"statement: %s", phrase, c.Source.Statement)
		}
	}
	if !strings.Contains(strings.ToLower(c.Product), "automation") {
		t.Errorf("product = %q, want it to name VCF Automation", c.Product)
	}
	if !strings.Contains(c.Release, "9.1") {
		t.Errorf("release = %q, want it to name the 9.1 release", c.Release)
	}
}

func TestContractRoutesMatchTheReference(t *testing.T) {
	c := loadContract(t)
	for _, id := range contract.RequiredOperations {
		op, err := c.Operation(id)
		if err != nil {
			t.Errorf("%v", err)
			continue
		}
		got := digest(op.Method + " " + normalizePath(op.Path))
		if got != opRouteDigest[id] {
			t.Errorf("operation %s: route %s %s does not match the documented route\n"+
				"(the expected route is pinned as a digest; re-read the reference page for this operation)",
				id, op.Method, op.Path)
		}
	}
}

func TestContractFieldsMatchTheReference(t *testing.T) {
	c := loadContract(t)
	if len(c.Operations) != len(contract.RequiredOperations) {
		t.Errorf("contract declares %d operations, want exactly %d", len(c.Operations), len(contract.RequiredOperations))
	}
	for _, id := range contract.RequiredOperations {
		op := c.MustOperation(id)
		if got, want := digest(semanticOperation(op)), operationShapeDigest[id]; got != want {
			t.Errorf("operation %s does not exactly match the documented parameter, body, and response semantics", id)
		}
	}

	auth := c.MustOperation(contract.OpAuthToken)
	if auth.RequestBody == nil {
		t.Fatalf("operation %s: declares no request body", auth.ID)
	}
	if auth.RequestBody.ContentType != contract.ContentTypeForm {
		t.Errorf("operation %s: request_body.content_type = %q, want %q",
			auth.ID, auth.RequestBody.ContentType, contract.ContentTypeForm)
	}
	requireDigests(t, "auth.token request body fields",
		contract.FieldNames(auth.RequestBody.Fields), authBodyFieldDigests)
	requireDigests(t, "auth.token response fields",
		contract.FieldNames(auth.Response.Fields), authResponseFieldDigests)

	req := c.MustOperation(contract.OpCatalogItemsRequest)
	if req.RequestBody == nil {
		t.Fatalf("operation %s: declares no request body", req.ID)
	}
	if req.RequestBody.ContentType != contract.ContentTypeJSON {
		t.Errorf("operation %s: request_body.content_type = %q, want %q",
			req.ID, req.RequestBody.ContentType, contract.ContentTypeJSON)
	}
	requireDigests(t, "catalog.items.request body fields",
		contract.FieldNames(req.RequestBody.Fields), catalogRequestBodyFieldDigests)
	for _, f := range req.RequestBody.Fields {
		if f.Required {
			t.Errorf("operation %s: body field %q is marked required, but the reference documents "+
				"every field of this body as optional", req.ID, f.Name)
		}
	}

	requireDigests(t, "deployments.list query parameters",
		contract.FieldNames(c.MustOperation(contract.OpDeploymentsList).Query), deploymentsListQueryDigests)
	requireDigests(t, "deployments.get query parameters",
		contract.FieldNames(c.MustOperation(contract.OpDeploymentsGet).Query), deploymentsGetQueryDigests)
	requireDigests(t, "catalog.items.list query parameters",
		contract.FieldNames(c.MustOperation(contract.OpCatalogItemsList).Query), catalogItemsListQueryDigests)
}

func TestOfficialSourcesRecordEveryOperation(t *testing.T) {
	c := loadContract(t)
	s, err := contract.LoadSources(sourcesPath)
	if err != nil {
		t.Fatalf("load %s: %v", sourcesPath, err)
	}
	if missing := s.Covers(c); len(missing) > 0 {
		sort.Strings(missing)
		t.Errorf("operations with no recorded source page: %s", strings.Join(missing, ", "))
	}
	seenPrimary := map[string]bool{}
	floor := time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC)
	for _, r := range s.Records {
		seenPrimary[digest(r.Operation+"|"+r.URL+"|"+r.Title)] = true
		when, err := time.Parse(contract.FetchedAtLayout, r.FetchedAt)
		if err != nil {
			t.Errorf("record for %s: fetched_at %q: %v", r.Operation, r.FetchedAt, err)
			continue
		}
		if when.Before(floor) {
			t.Errorf("record for %s: fetched_at %q predates VCF 9.x", r.Operation, r.FetchedAt)
		}
		if _, err := c.Operation(r.Operation); err != nil {
			t.Errorf("record cites unknown operation %q", r.Operation)
		}
		if !strings.HasPrefix(r.URL, "https://"+contract.SourcesHost+"/") {
			t.Errorf("record for %s: url %q is not a page on %s", r.Operation, r.URL, contract.SourcesHost)
		}
	}
	for id, want := range primarySourcePageDigest {
		if !seenPrimary[want] {
			t.Errorf("operation %s does not cite its canonical Broadcom reference page with that page's displayed title", id)
		}
	}
}

// --- mock ------------------------------------------------------------------

func startMock(t *testing.T, c *contract.Contract, expireAfter int) *mock.Server {
	t.Helper()
	srv, err := mock.Start(mock.Options{
		Contract:               c,
		Tenant:                 testTenant,
		APIToken:               testAPIToken,
		Deployments:            mock.Deployments(12),
		CatalogItems:           mock.CatalogItems(5),
		ExpireAccessTokenAfter: expireAfter,
	})
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(srv.Close)
	if !strings.HasPrefix(srv.URL(), "http://127.0.0.1:") {
		t.Fatalf("mock must listen on loopback, got %q", srv.URL())
	}
	return srv
}

func newClient(t *testing.T, c *contract.Contract, srv *mock.Server) *automation.Client {
	t.Helper()
	cl, err := automation.New(automation.Config{
		BaseURL:     srv.URL(),
		Tenant:      testTenant,
		APIToken:    testAPIToken,
		Contract:    c,
		Concurrency: 4,
	})
	if err != nil {
		t.Fatalf("new client: %v", err)
	}
	return cl
}

func TestMockServesOnlyWhatTheContractNames(t *testing.T) {
	c := loadContract(t)
	srv := startMock(t, c, 0)

	listPath := c.MustOperation(contract.OpDeploymentsList).Path
	reqOp := c.MustOperation(contract.OpCatalogItemsRequest)
	reqPath := expand(t, reqOp, mock.CatalogItemID(0))
	authOp := c.MustOperation(contract.OpAuthToken)

	tests := []struct {
		name   string
		method string
		path   string
		query  string
		body   string
		ctype  string
		want   int
	}{
		{"unrouted path is not served", http.MethodGet, "/definitely/not/an/operation", "", "", "", http.StatusNotFound},
		{"unrouted method on a known path", http.MethodDelete, listPath, "", "", "", http.StatusNotFound},
		{"token operation for another tenant", http.MethodPost, expand(t, authOp, "some-other-org"), "", "", contract.ContentTypeForm, http.StatusNotFound},
		{"undeclared query parameter", http.MethodGet, listPath, "notAParameter=1", "", "", http.StatusBadRequest},
		{"undeclared body field", http.MethodPost, reqPath, "", `{"notAField":1}`, contract.ContentTypeJSON, http.StatusBadRequest},
		{"JSON null is not a request object", http.MethodPost, reqPath, "", `null`, contract.ContentTypeJSON, http.StatusBadRequest},
		{"declared operation without a token", http.MethodGet, listPath, "", "", "", http.StatusUnauthorized},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			u := srv.URL() + tc.path
			if tc.query != "" {
				u += "?" + tc.query
			}
			var body io.Reader
			if tc.body != "" {
				body = strings.NewReader(tc.body)
			}
			req, err := http.NewRequest(tc.method, u, body)
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			if tc.ctype != "" {
				req.Header.Set("Content-Type", tc.ctype)
			}
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("%s %s: %v", tc.method, u, err)
			}
			defer resp.Body.Close()
			io.Copy(io.Discard, resp.Body)
			if resp.StatusCode != tc.want {
				t.Errorf("%s %s: status = %d, want %d", tc.method, tc.path, resp.StatusCode, tc.want)
			}
		})
	}
}

func TestMockLogsEveryRequestIncludingRejectedOnes(t *testing.T) {
	c := loadContract(t)
	srv := startMock(t, c, 0)

	resp, err := http.Get(srv.URL() + "/definitely/not/an/operation")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	resp.Body.Close()

	log := srv.Requests()
	if len(log) != 1 {
		t.Fatalf("request log has %d entries, want 1", len(log))
	}
	got := log[0]
	if got.Seq != 0 {
		t.Errorf("Seq = %d, want 0", got.Seq)
	}
	if got.Operation != "" {
		t.Errorf("Operation = %q, want \"\" for a request matching no operation", got.Operation)
	}
	if got.Status != http.StatusNotFound {
		t.Errorf("Status = %d, want %d", got.Status, http.StatusNotFound)
	}
	if got.Path != "/definitely/not/an/operation" {
		t.Errorf("Path = %q", got.Path)
	}
}

// --- the wire verifier ------------------------------------------------------

// TestWireCheckSeparatesAbsentFromEmpty pins the semantics the verifier exists
// for, without involving the client at all.
func TestWireCheckSeparatesAbsentFromEmpty(t *testing.T) {
	base := mock.RecordedRequest{
		Operation: "deployments.list",
		Method:    http.MethodGet,
		Path:      "/x",
		Status:    http.StatusOK,
	}

	tests := []struct {
		name    string
		got     mock.RecordedRequest
		want    wire.Expectation
		wantErr bool
	}{
		{
			name: "no query matches no query",
			got:  base,
			want: wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x"},
		},
		{
			name: "a parameter sent empty is not the same as a parameter omitted",
			got:  withQuery(base, url.Values{"search": {""}}),
			want: wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x"},
			// The request carried search=; the expectation says it carried
			// nothing. That must be reported, not tolerated.
			wantErr: true,
		},
		{
			name: "an omitted parameter is not the same as a parameter sent empty",
			got:  base,
			want: wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x",
				Query: url.Values{"search": {""}}},
			wantErr: true,
		},
		{
			name: "exact query matches",
			got:  withQuery(base, url.Values{"page": {"1"}, "size": {"4"}}),
			want: wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x",
				Query: url.Values{"page": {"1"}, "size": {"4"}}},
		},
		{
			name: "an extra parameter is rejected",
			got:  withQuery(base, url.Values{"page": {"1"}, "size": {"4"}}),
			want: wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x",
				Query: url.Values{"page": {"1"}}},
			wantErr: true,
		},
		{
			name: "empty JSON object matches empty JSON object",
			got:  withBody(base, `{}`),
			want: wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x", JSONBody: `{}`},
		},
		{
			name:    "a body key sent empty is not the same as a key omitted",
			got:     withBody(base, `{"reason":""}`),
			want:    wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x", JSONBody: `{}`},
			wantErr: true,
		},
		{
			name:    "a body key sent null is not the same as a key omitted",
			got:     withBody(base, `{"reason":null}`),
			want:    wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x", JSONBody: `{}`},
			wantErr: true,
		},
		{
			name:    "JSON null is not a JSON object",
			got:     withBody(base, `null`),
			want:    wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x", JSONBody: `{}`},
			wantErr: true,
		},
		{
			name: "JSON bodies compare by value, not by byte",
			got:  withBody(base, `{"b":2,"a":1}`),
			want: wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x",
				JSONBody: `{"a":1,"b":2}`},
		},
		{
			name:    "a wrong operation is rejected",
			got:     base,
			want:    wire.Expectation{Operation: "deployments.get", Method: http.MethodGet, Path: "/x"},
			wantErr: true,
		},
		{
			name:    "a wrong status is rejected",
			got:     base,
			want:    wire.Expectation{Operation: "deployments.list", Method: http.MethodGet, Path: "/x", Status: http.StatusCreated},
			wantErr: true,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := wire.Check(tc.got, tc.want)
			if tc.wantErr && err == nil {
				t.Fatal("wire.Check accepted a request that departs from the expectation")
			}
			if !tc.wantErr && err != nil {
				t.Fatalf("wire.Check rejected a matching request: %v", err)
			}
		})
	}
}

func withQuery(r mock.RecordedRequest, q url.Values) mock.RecordedRequest {
	r.Query = q
	return r
}

func withBody(r mock.RecordedRequest, body string) mock.RecordedRequest {
	r.Body = []byte(body)
	return r
}

// --- client wire shape ------------------------------------------------------

func TestUnsetOptionalFieldsNeverReachTheWire(t *testing.T) {
	c := loadContract(t)
	srv := startMock(t, c, 0)
	cl := newClient(t, c, srv)
	ctx := context.Background()

	if _, err := cl.ListDeployments(ctx, automation.ListDeploymentsOptions{}); err != nil {
		t.Fatalf("ListDeployments with no options: %v", err)
	}
	listed := srv.RequestsFor(contract.OpDeploymentsList)
	if len(listed) != 1 {
		t.Fatalf("deployments.list requests = %d, want 1", len(listed))
	}
	if n := len(listed[0].Query); n != 0 {
		t.Errorf("ListDeployments with no options sent %d query parameters (%s); "+
			"an option that was never set must not appear in the query string at all",
			n, listed[0].Query.Encode())
	}
	if err := wire.Check(listed[0], wire.Expectation{
		Operation: contract.OpDeploymentsList,
		Method:    http.MethodGet,
		Path:      c.MustOperation(contract.OpDeploymentsList).Path,
		Header:    map[string]string{"Authorization": "Bearer " + bearerOf(t, srv)},
		NoBody:    true,
		Status:    http.StatusOK,
	}); err != nil {
		t.Errorf("wire.Check: %v", err)
	}

	if _, err := cl.RequestCatalogItem(ctx, mock.CatalogItemID(0), automation.CatalogItemRequest{}); err != nil {
		t.Fatalf("RequestCatalogItem with an empty request: %v", err)
	}
	reqs := srv.RequestsFor(contract.OpCatalogItemsRequest)
	if len(reqs) != 1 {
		t.Fatalf("catalog.items.request requests = %d, want 1", len(reqs))
	}
	body, err := reqs[0].JSONBody()
	if err != nil {
		t.Fatalf("decode request body %q: %v", reqs[0].Body, err)
	}
	if len(body) != 0 {
		keys := make([]string, 0, len(body))
		for k := range body {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		t.Errorf("RequestCatalogItem with an empty request sent the keys [%s]; every field of this "+
			"body is optional, so an unset field must be absent from the object rather than present "+
			"and empty. body: %s", strings.Join(keys, ", "), reqs[0].Body)
	}

	// And when options *are* set, exactly those reach the wire — including a
	// value that happens to equal the zero value of its type.
	if _, err := cl.RequestCatalogItem(ctx, mock.CatalogItemID(1), automation.CatalogItemRequest{
		DeploymentName:   automation.Set("web-tier"),
		ProjectID:        automation.Set("proj-0"),
		BulkRequestCount: automation.Set(0),
		Reason:           automation.Set(""),
	}); err != nil {
		t.Fatalf("RequestCatalogItem: %v", err)
	}
	reqs = srv.RequestsFor(contract.OpCatalogItemsRequest)
	if len(reqs) != 2 {
		t.Fatalf("catalog.items.request requests = %d, want 2", len(reqs))
	}
	body, err = reqs[1].JSONBody()
	if err != nil {
		t.Fatalf("decode request body %q: %v", reqs[1].Body, err)
	}
	if len(body) != 4 {
		t.Errorf("expected exactly the 4 fields that were set, got body %s", reqs[1].Body)
	}
	assertBodyValue(t, body, reqs[1].Body, "web-tier")
	assertBodyValue(t, body, reqs[1].Body, "proj-0")
	assertBodyValue(t, body, reqs[1].Body, float64(0))
	assertBodyValue(t, body, reqs[1].Body, "")
}

// assertBodyValue checks that some key of the body carries want. The key names
// come from the contract, so the test asserts on values rather than on names
// it is not supposed to spell out.
func assertBodyValue(t *testing.T, body map[string]any, raw []byte, want any) {
	t.Helper()
	for _, v := range body {
		if reflect.DeepEqual(v, want) {
			return
		}
	}
	t.Errorf("request body does not carry the value %#v that was set on the request: %s", want, raw)
}

// bearerOf returns the most recent bearer token the client presented.
func bearerOf(t *testing.T, srv *mock.Server) string {
	t.Helper()
	var token string
	for _, r := range srv.Requests() {
		if a := r.Header.Get("Authorization"); strings.HasPrefix(a, "Bearer ") {
			token = strings.TrimPrefix(a, "Bearer ")
		}
	}
	if token == "" {
		t.Fatal("no bearer token was ever presented to the mock")
	}
	return token
}

func TestClientExchangesTheAPITokenBeforeItsFirstCall(t *testing.T) {
	c := loadContract(t)
	srv := startMock(t, c, 0)
	cl := newClient(t, c, srv)

	page, err := cl.ListDeployments(context.Background(), automation.ListDeploymentsOptions{
		Page: automation.Set(0),
		Size: automation.Set(4),
	})
	if err != nil {
		t.Fatalf("ListDeployments: %v", err)
	}
	if len(page.Content) != 4 {
		t.Fatalf("page has %d deployments, want 4", len(page.Content))
	}
	if page.TotalElements != 12 {
		t.Errorf("TotalElements = %d, want 12", page.TotalElements)
	}
	if page.Content[0].ID != mock.DeploymentID(0) {
		t.Errorf("first deployment ID = %q, want %q", page.Content[0].ID, mock.DeploymentID(0))
	}
	if page.Content[0].Name != "deployment-00" {
		t.Errorf("first deployment Name = %q, want %q", page.Content[0].Name, "deployment-00")
	}

	log := srv.Requests()
	if len(log) < 2 {
		t.Fatalf("expected a token exchange followed by the list call, got %d requests", len(log))
	}
	if log[0].Operation != contract.OpAuthToken {
		t.Fatalf("first request was %q, want the token exchange", log[0].Operation)
	}
	form, err := log[0].FormBody()
	if err != nil {
		t.Fatalf("token request body %q is not form-encoded: %v", log[0].Body, err)
	}
	if ct := log[0].Header.Get("Content-Type"); !strings.HasPrefix(ct, contract.ContentTypeForm) {
		t.Errorf("token request Content-Type = %q, want %q", ct, contract.ContentTypeForm)
	}
	var sawGrant, sawToken bool
	for _, vs := range form {
		for _, v := range vs {
			if digest(v) == grantTypeValueDigest {
				sawGrant = true
			}
			if v == testAPIToken {
				sawToken = true
			}
		}
	}
	if !sawGrant {
		t.Errorf("token request did not send the documented grant type; sent %q", log[0].Body)
	}
	if !sawToken {
		t.Error("token request did not send the configured API token")
	}
	if a := log[0].Header.Get("Authorization"); a != "" {
		t.Errorf("token request carried Authorization: %q; it is the call that obtains the token", a)
	}

	if auth := log[1].Header.Get("Authorization"); !strings.HasPrefix(auth, "Bearer ") {
		t.Errorf("Authorization = %q, want a Bearer token", auth)
	}
}

// --- token expiry -----------------------------------------------------------

// TestTokenExpiryMidRunLosesNoWork walks five pages of deployments with a token
// that stops being accepted after two calls. The walk must refresh and carry
// on from where it was: every page read exactly once, nothing collected
// discarded, nothing re-read.
func TestTokenExpiryMidRunLosesNoWork(t *testing.T) {
	c := loadContract(t)
	srv := startMock(t, c, 2)
	cl := newClient(t, c, srv)

	got, err := cl.CollectDeployments(context.Background(), automation.ListDeploymentsOptions{
		Page: automation.Set(0),
		Size: automation.Set(4),
	})
	if err != nil {
		t.Fatalf("CollectDeployments: %v", err)
	}

	if len(got) != 12 {
		t.Fatalf("collected %d deployments, want all 12", len(got))
	}
	for i, d := range got {
		if d.ID != mock.DeploymentID(i) {
			t.Fatalf("deployment %d has ID %q, want %q: the walk did not return every page in order",
				i, d.ID, mock.DeploymentID(i))
		}
	}

	// Each page fetched successfully exactly once.
	pages := map[string]int{}
	var unauthorized int
	for _, r := range srv.RequestsFor(contract.OpDeploymentsList) {
		switch r.Status {
		case http.StatusOK:
			pages[pageParam(r)]++
		case http.StatusUnauthorized:
			unauthorized++
		default:
			t.Errorf("deployments.list returned unexpected status %d", r.Status)
		}
	}
	if len(pages) != 3 {
		t.Errorf("read %d distinct pages, want 3: %v", len(pages), pages)
	}
	for p, n := range pages {
		if n != 1 {
			t.Errorf("page %s was read successfully %d times; a page already read must not be read again", p, n)
		}
	}
	if unauthorized != 1 {
		t.Errorf("saw %d rejected requests, want exactly 1 (the call that met the expired token)", unauthorized)
	}
	if n := len(srv.RequestsFor(contract.OpAuthToken)); n != 2 {
		t.Errorf("token exchanges = %d, want 2 (the initial one, and one refresh)", n)
	}
}

// pageParam returns the value of whichever query parameter carried the page
// index, identified by its digest rather than by name.
func pageParam(r mock.RecordedRequest) string {
	for k, vs := range r.Query {
		if digest(k) == pageParamDigest && len(vs) > 0 {
			return vs[0]
		}
	}
	return "<absent>"
}

// TestConcurrentCallersRefreshTheTokenOnce fetches many deployments at once
// with a token that expires part way through. Every fetch must succeed, and
// the callers that meet the expired token must between them cause one refresh
// each time it expires — not one refresh per caller.
func TestConcurrentCallersRefreshTheTokenOnce(t *testing.T) {
	c := loadContract(t)
	const expireAfter = 3
	srv := startMock(t, c, expireAfter)
	cl := newClient(t, c, srv)

	ids := make([]string, 9)
	for i := range ids {
		ids[i] = mock.DeploymentID(i)
	}

	got, err := cl.CollectDeploymentDetails(context.Background(), ids, automation.GetDeploymentOptions{})
	if err != nil {
		t.Fatalf("CollectDeploymentDetails: %v", err)
	}
	if len(got) != len(ids) {
		t.Fatalf("got %d deployments, want %d", len(got), len(ids))
	}
	for i, d := range got {
		if d.ID != ids[i] {
			t.Errorf("result %d has ID %q, want %q: results must come back in the order requested",
				i, d.ID, ids[i])
		}
	}

	// Nine authorized fetches, plus the token exchanges themselves, at three
	// authorized requests per token: three tokens are needed and three is
	// what a caller that coalesces its refreshes will ask for.
	wantExchanges := 3
	if n := len(srv.RequestsFor(contract.OpAuthToken)); n != wantExchanges {
		t.Errorf("token exchanges = %d, want %d. Callers that discover the same expired token at the "+
			"same moment must cause one exchange between them, not one each.", n, wantExchanges)
	}

	ok := map[string]int{}
	for _, r := range srv.RequestsFor(contract.OpDeploymentsGet) {
		if r.Status == http.StatusOK {
			ok[r.Path]++
		}
	}
	if len(ok) != len(ids) {
		t.Errorf("fetched %d distinct deployments, want %d", len(ok), len(ids))
	}
	for p, n := range ok {
		if n != 1 {
			t.Errorf("%s was fetched successfully %d times, want 1", p, n)
		}
	}
}

// --- the task's own tests ---------------------------------------------------

func TestPackagesCarryTableDrivenTests(t *testing.T) {
	for _, pkg := range []string{"automation", "mock", "wire"} {
		matches, err := filepath.Glob(filepath.Join("..", pkg, "*_test.go"))
		if err != nil {
			t.Fatalf("glob %s: %v", pkg, err)
		}
		if len(matches) == 0 {
			t.Errorf("package %s has no test file", pkg)
			continue
		}
		var tabular bool
		for _, m := range matches {
			b, err := os.ReadFile(m)
			if err != nil {
				t.Fatalf("read %s: %v", m, err)
			}
			if strings.Contains(string(b), "[]struct {") || strings.Contains(string(b), "[]struct{") {
				tabular = true
			}
		}
		if !tabular {
			t.Errorf("package %s has tests, but none of them is table-driven", pkg)
		}
	}
}

func TestDocsAreValidJSON(t *testing.T) {
	for _, p := range []string{contractPath, sourcesPath} {
		b, err := os.ReadFile(p)
		if err != nil {
			t.Fatalf("read %s: %v", p, err)
		}
		var v any
		if err := json.Unmarshal(b, &v); err != nil {
			t.Errorf("%s is not valid JSON: %v", p, err)
		}
		if !strings.HasSuffix(string(b), "\n") {
			t.Errorf("%s does not end with a newline", p)
		}
	}
}
