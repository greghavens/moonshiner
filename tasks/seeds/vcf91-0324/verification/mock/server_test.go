package mock

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"vcfauto/contract"
)

const (
	testTenant   = "acme-org"
	testAPIToken = "vcfa-api-token-fixture-0123456789"
)

func testContract(t *testing.T) *contract.Contract {
	t.Helper()
	c, err := contract.Load("../docs/contract.json")
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	return c
}

func start(t *testing.T, expireAfter int) (*Server, *contract.Contract) {
	t.Helper()
	c := testContract(t)
	s, err := Start(Options{
		Contract:               c,
		Tenant:                 testTenant,
		APIToken:               testAPIToken,
		Deployments:            Deployments(12),
		CatalogItems:           CatalogItems(5),
		ExpireAccessTokenAfter: expireAfter,
	})
	if err != nil {
		t.Fatalf("start: %v", err)
	}
	t.Cleanup(s.Close)
	return s, c
}

// token exchanges the API token and returns the access token.
func token(t *testing.T, s *Server, c *contract.Contract) string {
	t.Helper()
	op := c.MustOperation(contract.OpAuthToken)
	path, err := op.ExpandPath(map[string]string{"tenant": testTenant})
	if err != nil {
		t.Fatalf("expand: %v", err)
	}
	resp, err := http.Post(s.URL()+path, contract.ContentTypeForm,
		strings.NewReader("grant_type=refresh_token&refresh_token="+testAPIToken))
	if err != nil {
		t.Fatalf("token: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("token: status %d: %s", resp.StatusCode, body)
	}
	var out struct {
		AccessToken string `json:"access_token"`
		TokenType   string `json:"token_type"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode token: %v", err)
	}
	if out.TokenType != "Bearer" || out.ExpiresIn != 3600 {
		t.Errorf("token_type = %q, expires_in = %d", out.TokenType, out.ExpiresIn)
	}
	return out.AccessToken
}

func TestStartRejectsIncompleteOptions(t *testing.T) {
	c := testContract(t)
	tests := []struct {
		name string
		opts Options
	}{
		{"no contract", Options{Tenant: testTenant, APIToken: testAPIToken}},
		{"no tenant", Options{Contract: c, APIToken: testAPIToken}},
		{"no API token", Options{Contract: c, Tenant: testTenant}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			s, err := Start(tc.opts)
			if err == nil {
				s.Close()
				t.Fatal("Start() = nil error, want a rejection")
			}
		})
	}
}

func TestRoutingAndValidation(t *testing.T) {
	s, c := start(t, 0)
	tok := token(t, s, c)

	listPath := c.MustOperation(contract.OpDeploymentsList).Path
	getPath := listPath + "/" + DeploymentID(0)
	reqPath, err := c.MustOperation(contract.OpCatalogItemsRequest).
		ExpandPath(map[string]string{"id": CatalogItemID(0)})
	if err != nil {
		t.Fatalf("expand: %v", err)
	}

	tests := []struct {
		name    string
		method  string
		path    string
		query   string
		body    string
		ctype   string
		bearer  string
		want    int
		wantOp  string
		wantLog bool
	}{
		{name: "unknown path", method: http.MethodGet, path: "/nope", bearer: tok,
			want: http.StatusNotFound, wantOp: ""},
		{name: "undocumented method", method: http.MethodPut, path: listPath, bearer: tok,
			want: http.StatusNotFound},
		{name: "other tenant", method: http.MethodPost, path: "/tm/oauth/tenant/other/token",
			want: http.StatusNotFound},
		{name: "undeclared query parameter", method: http.MethodGet, path: listPath,
			query: "nope=1", bearer: tok, want: http.StatusBadRequest, wantOp: contract.OpDeploymentsList},
		{name: "declared query parameter", method: http.MethodGet, path: listPath,
			query: "page=0&size=4", bearer: tok, want: http.StatusOK, wantOp: contract.OpDeploymentsList},
		{name: "undeclared body field", method: http.MethodPost, path: reqPath,
			body: `{"nope":1}`, ctype: contract.ContentTypeJSON, bearer: tok,
			want: http.StatusBadRequest, wantOp: contract.OpCatalogItemsRequest},
		{name: "declared body field", method: http.MethodPost, path: reqPath,
			body: `{"deploymentName":"d"}`, ctype: contract.ContentTypeJSON, bearer: tok,
			want: http.StatusOK, wantOp: contract.OpCatalogItemsRequest},
		{name: "no bearer token", method: http.MethodGet, path: listPath,
			want: http.StatusUnauthorized, wantOp: contract.OpDeploymentsList},
		{name: "unknown bearer token", method: http.MethodGet, path: listPath, bearer: "made-up",
			want: http.StatusUnauthorized, wantOp: contract.OpDeploymentsList},
		{name: "get by id", method: http.MethodGet, path: getPath, bearer: tok,
			want: http.StatusOK, wantOp: contract.OpDeploymentsGet},
		{name: "get by unknown id", method: http.MethodGet, path: listPath + "/missing", bearer: tok,
			want: http.StatusNotFound, wantOp: contract.OpDeploymentsGet},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			before := len(s.Requests())

			u := s.URL() + tc.path
			if tc.query != "" {
				u += "?" + tc.query
			}
			var rdr io.Reader
			if tc.body != "" {
				rdr = strings.NewReader(tc.body)
			}
			req, err := http.NewRequest(tc.method, u, rdr)
			if err != nil {
				t.Fatalf("build: %v", err)
			}
			if tc.ctype != "" {
				req.Header.Set("Content-Type", tc.ctype)
			}
			if tc.bearer != "" {
				req.Header.Set("Authorization", "Bearer "+tc.bearer)
			}
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("do: %v", err)
			}
			defer resp.Body.Close()
			body, _ := io.ReadAll(resp.Body)
			if resp.StatusCode != tc.want {
				t.Fatalf("status = %d, want %d: %s", resp.StatusCode, tc.want, body)
			}

			log := s.Requests()
			if len(log) != before+1 {
				t.Fatalf("log grew by %d, want 1", len(log)-before)
			}
			last := log[len(log)-1]
			if last.Operation != tc.wantOp {
				t.Errorf("logged operation = %q, want %q", last.Operation, tc.wantOp)
			}
			if last.Status != tc.want {
				t.Errorf("logged status = %d, want %d", last.Status, tc.want)
			}
			if last.Seq != len(log)-1 {
				t.Errorf("Seq = %d, want %d", last.Seq, len(log)-1)
			}
		})
	}
}

func TestTokenExchange(t *testing.T) {
	s, c := start(t, 0)
	op := c.MustOperation(contract.OpAuthToken)
	path, err := op.ExpandPath(map[string]string{"tenant": testTenant})
	if err != nil {
		t.Fatalf("expand: %v", err)
	}

	tests := []struct {
		name string
		form string
		want int
	}{
		{"valid", "grant_type=refresh_token&refresh_token=" + testAPIToken, http.StatusOK},
		{"wrong grant", "grant_type=password&refresh_token=" + testAPIToken, http.StatusBadRequest},
		{"wrong API token", "grant_type=refresh_token&refresh_token=nope", http.StatusBadRequest},
		{"empty body", "", http.StatusBadRequest},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			resp, err := http.Post(s.URL()+path, contract.ContentTypeForm, strings.NewReader(tc.form))
			if err != nil {
				t.Fatalf("post: %v", err)
			}
			defer resp.Body.Close()
			io.Copy(io.Discard, resp.Body)
			if resp.StatusCode != tc.want {
				t.Fatalf("status = %d, want %d", resp.StatusCode, tc.want)
			}
		})
	}
}

func TestAccessTokenExpiresAfterAFixedNumberOfRequests(t *testing.T) {
	const expireAfter = 2
	s, c := start(t, expireAfter)
	tok := token(t, s, c)
	listPath := c.MustOperation(contract.OpDeploymentsList).Path

	tests := []struct {
		name string
		want int
	}{
		{"first authorized request", http.StatusOK},
		{"second authorized request", http.StatusOK},
		{"third request meets the expiry", http.StatusUnauthorized},
		{"and stays expired", http.StatusUnauthorized},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(http.MethodGet, s.URL()+listPath, nil)
			if err != nil {
				t.Fatalf("build: %v", err)
			}
			req.Header.Set("Authorization", "Bearer "+tok)
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("do: %v", err)
			}
			defer resp.Body.Close()
			io.Copy(io.Discard, resp.Body)
			if resp.StatusCode != tc.want {
				t.Fatalf("status = %d, want %d", resp.StatusCode, tc.want)
			}
		})
	}

	// A fresh exchange restores service.
	tok2 := token(t, s, c)
	if tok2 == tok {
		t.Fatal("a second exchange returned the same access token")
	}
}

func TestPaging(t *testing.T) {
	s, c := start(t, 0)
	tok := token(t, s, c)
	listPath := c.MustOperation(contract.OpDeploymentsList).Path

	tests := []struct {
		name       string
		query      string
		wantCount  int
		wantFirst  bool
		wantLast   bool
		wantTotal  int
		wantPages  int
		wantNumber int
	}{
		{name: "first page", query: "page=0&size=5", wantCount: 5, wantFirst: true,
			wantTotal: 12, wantPages: 3, wantNumber: 0},
		{name: "middle page", query: "page=1&size=5", wantCount: 5,
			wantTotal: 12, wantPages: 3, wantNumber: 1},
		{name: "short last page", query: "page=2&size=5", wantCount: 2, wantLast: true,
			wantTotal: 12, wantPages: 3, wantNumber: 2},
		{name: "past the end", query: "page=9&size=5", wantCount: 0, wantLast: true,
			wantTotal: 12, wantPages: 3, wantNumber: 9},
		{name: "contract defaults when omitted", query: "", wantCount: 12, wantFirst: true,
			wantLast: true, wantTotal: 12, wantPages: 1, wantNumber: 0},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			u := s.URL() + listPath
			if tc.query != "" {
				u += "?" + tc.query
			}
			req, _ := http.NewRequest(http.MethodGet, u, nil)
			req.Header.Set("Authorization", "Bearer "+tok)
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("do: %v", err)
			}
			defer resp.Body.Close()

			var page struct {
				Content       []map[string]any `json:"content"`
				Number        int              `json:"number"`
				First         bool             `json:"first"`
				Last          bool             `json:"last"`
				TotalElements int              `json:"totalElements"`
				TotalPages    int              `json:"totalPages"`
			}
			if err := json.NewDecoder(resp.Body).Decode(&page); err != nil {
				t.Fatalf("decode: %v", err)
			}
			if len(page.Content) != tc.wantCount {
				t.Errorf("content = %d items, want %d", len(page.Content), tc.wantCount)
			}
			if page.Number != tc.wantNumber {
				t.Errorf("number = %d, want %d", page.Number, tc.wantNumber)
			}
			if page.First != tc.wantFirst {
				t.Errorf("first = %v, want %v", page.First, tc.wantFirst)
			}
			if page.Last != tc.wantLast {
				t.Errorf("last = %v, want %v", page.Last, tc.wantLast)
			}
			if page.TotalElements != tc.wantTotal {
				t.Errorf("totalElements = %d, want %d", page.TotalElements, tc.wantTotal)
			}
			if page.TotalPages != tc.wantPages {
				t.Errorf("totalPages = %d, want %d", page.TotalPages, tc.wantPages)
			}
		})
	}
}
