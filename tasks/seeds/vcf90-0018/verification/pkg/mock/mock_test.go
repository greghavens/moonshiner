package mock

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
)

const (
	testUser       = "administrator@vsphere.local"
	testPass       = "VMw@re1!VMw@re1!"
	testAccess     = "access-1"
	testRefreshed  = "access-2"
	testRefreshID  = "refresh-1"
	contractPath   = "../../docs/contract.json"
	fixturePath    = "../../testdata/credentials.json"
	authorization  = "Authorization"
	jsonContent    = "application/json"
	contentTypeHdr = "Content-Type"
)

func newTestServer(t *testing.T, expireAfter int) *Server {
	t.Helper()
	s, err := New(Config{
		ContractPath:   contractPath,
		FixturePath:    fixturePath,
		Username:       testUser,
		Password:       testPass,
		AccessToken:    testAccess,
		RefreshedToken: testRefreshed,
		RefreshTokenID: testRefreshID,
		ExpireAfter:    expireAfter,
	})
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	t.Cleanup(s.Close)
	return s
}

func send(t *testing.T, s *Server, method, target, body string, header map[string]string) *http.Response {
	t.Helper()
	var reader io.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, s.URL()+target, reader)
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	for k, v := range header {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, target, err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func TestNewRejectsIncompleteConfig(t *testing.T) {
	tests := []struct {
		name string
		cfg  Config
	}{
		{"no contract", Config{FixturePath: fixturePath, AccessToken: "a", RefreshedToken: "b", RefreshTokenID: "c"}},
		{"no fixture", Config{ContractPath: contractPath, AccessToken: "a", RefreshedToken: "b", RefreshTokenID: "c"}},
		{"no tokens", Config{ContractPath: contractPath, FixturePath: fixturePath}},
		{"contract missing", Config{ContractPath: "does-not-exist.json", FixturePath: fixturePath, AccessToken: "a", RefreshedToken: "b", RefreshTokenID: "c"}},
		{"fixture missing", Config{ContractPath: contractPath, FixturePath: "does-not-exist.json", AccessToken: "a", RefreshedToken: "b", RefreshTokenID: "c"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s, err := New(tt.cfg)
			if err == nil {
				s.Close()
				t.Fatal("expected an error")
			}
		})
	}
}

func TestRoutingIsPinnedToTheContract(t *testing.T) {
	bearer := map[string]string{authorization: "Bearer " + testAccess}
	jsonBody := map[string]string{contentTypeHdr: jsonContent}

	tests := []struct {
		name       string
		method     string
		target     string
		body       string
		header     map[string]string
		wantStatus int
		wantOpID   string
	}{
		{"createToken", http.MethodPost, "/v1/tokens", `{"username":"` + testUser + `","password":"` + testPass + `"}`, jsonBody, http.StatusCreated, "createToken"},
		{"createToken with a bad password", http.MethodPost, "/v1/tokens", `{"username":"` + testUser + `","password":"nope"}`, jsonBody, http.StatusBadRequest, "createToken"},
		{"createToken with an unknown field", http.MethodPost, "/v1/tokens", `{"username":"u","password":"p","tenant":"t"}`, jsonBody, http.StatusBadRequest, "createToken"},
		{"createToken without a json content type", http.MethodPost, "/v1/tokens", `{}`, nil, http.StatusBadRequest, "createToken"},
		{"refreshAccessToken", http.MethodPatch, "/v1/tokens/access-token/refresh", `"` + testRefreshID + `"`, jsonBody, http.StatusOK, "refreshAccessToken"},
		{"refreshAccessToken with an unknown id", http.MethodPatch, "/v1/tokens/access-token/refresh", `"other"`, jsonBody, http.StatusNotFound, "refreshAccessToken"},
		{"refreshAccessToken with an object body", http.MethodPatch, "/v1/tokens/access-token/refresh", `{"id":"` + testRefreshID + `"}`, jsonBody, http.StatusBadRequest, "refreshAccessToken"},
		{"getCredentials", http.MethodGet, "/v1/credentials?pageNumber=0", "", bearer, http.StatusOK, "getCredentials"},
		{"getCredentials without a token", http.MethodGet, "/v1/credentials?pageNumber=0", "", nil, http.StatusUnauthorized, "getCredentials"},
		{"getCredentials with an unknown token", http.MethodGet, "/v1/credentials?pageNumber=0", "", map[string]string{authorization: "Bearer nope"}, http.StatusUnauthorized, "getCredentials"},
		{"getCredentials with a basic auth header", http.MethodGet, "/v1/credentials?pageNumber=0", "", map[string]string{authorization: "Basic abc"}, http.StatusUnauthorized, "getCredentials"},
		{"getCredentials with an unknown parameter", http.MethodGet, "/v1/credentials?tenant=a", "", bearer, http.StatusBadRequest, "getCredentials"},
		{"getCredentials with an empty parameter", http.MethodGet, "/v1/credentials?resourceType=", "", bearer, http.StatusBadRequest, "getCredentials"},
		{"getCredentials with a repeated parameter", http.MethodGet, "/v1/credentials?pageNumber=0&pageNumber=1", "", bearer, http.StatusBadRequest, "getCredentials"},
		{"getCredentials with a negative page", http.MethodGet, "/v1/credentials?pageNumber=-1", "", bearer, http.StatusBadRequest, "getCredentials"},
		{"an operation the contract does not name", http.MethodGet, "/v1/domains", "", bearer, http.StatusNotFound, ""},
		{"a method the contract does not name", http.MethodPatch, "/v1/credentials", `{}`, jsonBody, http.StatusNotFound, ""},
		{"the root", http.MethodGet, "/", "", nil, http.StatusNotFound, ""},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s := newTestServer(t, 1000)
			resp := send(t, s, tt.method, tt.target, tt.body, tt.header)
			if resp.StatusCode != tt.wantStatus {
				t.Errorf("status = %d, want %d", resp.StatusCode, tt.wantStatus)
			}
			log := s.Requests()
			if len(log) != 1 {
				t.Fatalf("request log has %d entries, want 1", len(log))
			}
			if log[0].OperationID != tt.wantOpID {
				t.Errorf("operationId = %q, want %q", log[0].OperationID, tt.wantOpID)
			}
			if log[0].Status != tt.wantStatus {
				t.Errorf("logged status = %d, want %d", log[0].Status, tt.wantStatus)
			}
			if log[0].Index != 0 {
				t.Errorf("index = %d, want 0", log[0].Index)
			}
		})
	}
}

func TestPagingAndFiltering(t *testing.T) {
	tests := []struct {
		name          string
		query         string
		wantIDSuffix  []string
		wantTotal     int
		wantTotalPage int
	}{
		{"everything on one page", "pageNumber=0", nil, 12, 1},
		{"first page of two", "pageNumber=0&pageSize=6", nil, 12, 2},
		{"second page of two", "pageNumber=1&pageSize=6", nil, 12, 2},
		{"filtered by resource type", "pageNumber=0&resourceType=ESXI", nil, 8, 1},
		{"filtered by account type", "pageNumber=0&accountType=SYSTEM", nil, 2, 1},
		{"filtered by domain", "pageNumber=0&domainName=wld-02", nil, 2, 1},
		{"filtered by resource name", "pageNumber=0&resourceName=esxi-01.vrack.vsphere.local", nil, 1, 1},
		{"filter matching nothing", "pageNumber=0&resourceType=NSX_ALB", nil, 0, 0},
		{"page past the end", "pageNumber=9&pageSize=6", nil, 12, 2},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s := newTestServer(t, 1000)
			resp := send(t, s, http.MethodGet, "/v1/credentials?"+tt.query, "",
				map[string]string{authorization: "Bearer " + testAccess})
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("status = %d, want 200", resp.StatusCode)
			}
			var page struct {
				Elements     []credential `json:"elements"`
				PageMetadata pageMetadata `json:"pageMetadata"`
			}
			if err := json.NewDecoder(resp.Body).Decode(&page); err != nil {
				t.Fatalf("decode: %v", err)
			}
			if page.PageMetadata.TotalElements != tt.wantTotal {
				t.Errorf("totalElements = %d, want %d", page.PageMetadata.TotalElements, tt.wantTotal)
			}
			if page.PageMetadata.TotalPages != tt.wantTotalPage {
				t.Errorf("totalPages = %d, want %d", page.PageMetadata.TotalPages, tt.wantTotalPage)
			}
			if page.PageMetadata.PageSize != len(page.Elements) {
				t.Errorf("pageSize = %d, but the page carried %d elements", page.PageMetadata.PageSize, len(page.Elements))
			}
		})
	}
}

func TestAccessTokenExpiresAfterTheConfiguredNumberOfRequests(t *testing.T) {
	tests := []struct {
		name        string
		expireAfter int
		wantStatus  []int
	}{
		{"never used before expiry", 0, []int{http.StatusUnauthorized}},
		{"one good request", 1, []int{http.StatusOK, http.StatusUnauthorized}},
		{"three good requests", 3, []int{http.StatusOK, http.StatusOK, http.StatusOK, http.StatusUnauthorized}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s := newTestServer(t, tt.expireAfter)
			for i, want := range tt.wantStatus {
				resp := send(t, s, http.MethodGet, "/v1/credentials?pageNumber=0", "",
					map[string]string{authorization: "Bearer " + testAccess})
				if resp.StatusCode != want {
					t.Fatalf("request %d: status = %d, want %d", i, resp.StatusCode, want)
				}
			}
			// The refreshed token is always accepted.
			resp := send(t, s, http.MethodGet, "/v1/credentials?pageNumber=0", "",
				map[string]string{authorization: "Bearer " + testRefreshed})
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("refreshed token: status = %d, want 200", resp.StatusCode)
			}
		})
	}
}

func TestRefreshReturnsABareJSONString(t *testing.T) {
	s := newTestServer(t, 0)
	resp := send(t, s, http.MethodPatch, "/v1/tokens/access-token/refresh", `"`+testRefreshID+`"`,
		map[string]string{contentTypeHdr: jsonContent})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	var token string
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(raw))), &token); err != nil {
		t.Fatalf("response %q is not a bare JSON string: %v", raw, err)
	}
	if token != testRefreshed {
		t.Errorf("token = %q, want %q", token, testRefreshed)
	}
}

func TestRequestsReturnsAnIndependentSnapshot(t *testing.T) {
	s := newTestServer(t, 1000)
	send(t, s, http.MethodPost, "/v1/tokens", `{"username":"`+testUser+`","password":"`+testPass+`"}`,
		map[string]string{contentTypeHdr: jsonContent})

	first := s.Requests()
	if len(first) != 1 {
		t.Fatalf("request log has %d entries, want 1", len(first))
	}
	body := string(first[0].Body)
	first[0].Body[0] = '#'
	first[0].Header.Set(contentTypeHdr, "text/plain")
	first[0].OperationID = "tampered"

	second := s.Requests()
	if string(second[0].Body) != body {
		t.Error("the logged body can be mutated through the returned snapshot")
	}
	if second[0].Header.Get(contentTypeHdr) != jsonContent {
		t.Error("the logged headers can be mutated through the returned snapshot")
	}
	if second[0].OperationID != "createToken" {
		t.Error("the logged operationId can be mutated through the returned snapshot")
	}
}
