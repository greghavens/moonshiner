// This file is part of the protected harness. Do not edit it.
package verify

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"

	"vcfopsnetinv/internal/opsnet"
	"vcfopsnetinv/internal/opsnetmock"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"
)

type contractDoc struct {
	Spec struct {
		Repository string `json:"repository"`
		Path       string `json:"path"`
		CommitSHA  string `json:"commit_sha"`
		Version    string `json:"version"`
		License    string `json:"license"`
	} `json:"spec"`
	BasePath string `json:"basePath"`
	Security struct {
		SchemeName  string `json:"schemeName"`
		In          string `json:"in"`
		HeaderName  string `json:"headerName"`
		ValueFormat string `json:"valueFormat"`
	} `json:"security"`
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

type sourcesDoc struct {
	Source struct {
		Kind       string `json:"kind"`
		Repository string `json:"repository"`
		License    string `json:"license"`
		SpecPath   string `json:"spec_path"`
		CommitSHA  string `json:"commit_sha"`
	} `json:"source"`
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

func loadJSON(t *testing.T, path string, into any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	// Only the fields the structs above declare are validated; the documents
	// carry extra descriptive keys that are deliberately ignored here.
	if err := json.NewDecoder(bytes.NewReader(raw)).Decode(into); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
}

func routeKey(opID, method, path string) string {
	return opID + " " + method + " " + path
}

// TestMockIsPinnedToContract asserts the mock serves exactly the operations the
// contract names - no more, no fewer.
func TestMockIsPinnedToContract(t *testing.T) {
	var contract contractDoc
	loadJSON(t, contractPath, &contract)
	if len(contract.Operations) == 0 {
		t.Fatal("docs/contract.json declares no operations")
	}

	var want, got []string
	for _, op := range contract.Operations {
		want = append(want, routeKey(op.OperationID, op.Method, op.Path))
	}
	for _, r := range opsnetmock.ContractOperations() {
		got = append(got, routeKey(r.OperationID, r.Method, r.Path))
	}
	sort.Strings(want)
	sort.Strings(got)
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Errorf("mock routes do not match docs/contract.json\n got:\n%s\nwant:\n%s",
			strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
}

// TestContractBasePathAndSecurity asserts the constants the client compiles
// against agree with the contract file.
func TestContractBasePathAndSecurity(t *testing.T) {
	var contract contractDoc
	loadJSON(t, contractPath, &contract)

	if contract.BasePath != opsnet.SpecBasePath {
		t.Errorf("contract basePath = %q, opsnet.SpecBasePath = %q", contract.BasePath, opsnet.SpecBasePath)
	}
	if contract.BasePath != opsnetmock.BasePath {
		t.Errorf("contract basePath = %q, opsnetmock.BasePath = %q", contract.BasePath, opsnetmock.BasePath)
	}
	if contract.Security.HeaderName != "Authorization" || contract.Security.In != "header" {
		t.Errorf("contract security = %q in %q, want Authorization in header",
			contract.Security.HeaderName, contract.Security.In)
	}
	wantPrefix := strings.TrimSuffix(contract.Security.ValueFormat, "{token}")
	if wantPrefix == contract.Security.ValueFormat || wantPrefix == "" {
		t.Fatalf("contract security valueFormat = %q, want a %q placeholder", contract.Security.ValueFormat, "{token}")
	}
	if opsnet.AuthHeaderPrefix != wantPrefix {
		t.Errorf("opsnet.AuthHeaderPrefix = %q, contract requires %q", opsnet.AuthHeaderPrefix, wantPrefix)
	}
	if opsnetmock.AuthPrefix != wantPrefix {
		t.Errorf("opsnetmock.AuthPrefix = %q, contract requires %q", opsnetmock.AuthPrefix, wantPrefix)
	}
}

var shaRE = regexp.MustCompile(`^[0-9a-f]{40}$`)

// TestOfficialSourcesRecordProvenance asserts the contract is traceable to a
// specific revision of a specification document rather than to a doc page.
func TestOfficialSourcesRecordProvenance(t *testing.T) {
	var contract contractDoc
	var sources sourcesDoc
	loadJSON(t, contractPath, &contract)
	loadJSON(t, sourcesPath, &sources)

	if sources.Source.Kind != "openapi-specification" {
		t.Errorf("official_sources kind = %q, want %q", sources.Source.Kind, "openapi-specification")
	}
	if sources.Source.Repository == "" {
		t.Error("official_sources records no repository")
	}
	if sources.Source.License != "Apache-2.0" {
		t.Errorf("official_sources license = %q, want Apache-2.0", sources.Source.License)
	}
	if !strings.HasSuffix(sources.Source.SpecPath, ".yaml") && !strings.HasSuffix(sources.Source.SpecPath, ".json") {
		t.Errorf("official_sources spec_path = %q, want a specification document path", sources.Source.SpecPath)
	}
	if sources.Source.SpecPath != contract.Spec.Path {
		t.Errorf("spec path disagrees: official_sources %q vs contract %q", sources.Source.SpecPath, contract.Spec.Path)
	}
	if !shaRE.MatchString(sources.Source.CommitSHA) {
		t.Errorf("official_sources commit_sha = %q, want 40 lowercase hex characters", sources.Source.CommitSHA)
	}
	if sources.Source.CommitSHA != contract.Spec.CommitSHA {
		t.Errorf("commit sha disagrees: official_sources %q vs contract %q",
			sources.Source.CommitSHA, contract.Spec.CommitSHA)
	}

	recorded := map[string]bool{}
	for _, op := range sources.Operations {
		recorded[routeKey(op.OperationID, op.Method, op.Path)] = true
	}
	for _, op := range contract.Operations {
		key := routeKey(op.OperationID, op.Method, op.Path)
		if !recorded[key] {
			t.Errorf("official_sources does not record operation %q", key)
		}
	}
	if len(sources.Operations) != len(contract.Operations) {
		t.Errorf("official_sources records %d operations, contract names %d",
			len(sources.Operations), len(contract.Operations))
	}
}

// TestMockRejectsOffContractRequests proves the mock serves only the contract
// operations, so a client that strays cannot pass by accident.
func TestMockRejectsOffContractRequests(t *testing.T) {
	srv := opsnetmock.New(opsnetmock.Options{Applications: 3})
	t.Cleanup(srv.Close)

	token := loginDirect(t, srv)

	cases := []struct {
		name   string
		method string
		path   string
		header string
		want   int
	}{
		{"unknown_path", http.MethodGet, opsnetmock.BasePath + "/infra/vms", token, http.StatusNotFound},
		{"missing_base_path", http.MethodGet, "/groups/applications", token, http.StatusNotFound},
		{"other_operation_in_spec", http.MethodGet, opsnetmock.BasePath + "/info/version", token, http.StatusNotFound},
		{"wrong_method_on_applications", http.MethodPost, opsnetmock.BasePath + "/groups/applications", token, http.StatusMethodNotAllowed},
		{"wrong_method_on_token", http.MethodGet, opsnetmock.BasePath + "/auth/token", token, http.StatusMethodNotAllowed},
		{"undeclared_query_parameter", http.MethodGet, opsnetmock.BasePath + "/groups/applications?limit=5", token, http.StatusBadRequest},
		{"empty_cursor_is_not_a_cursor", http.MethodGet, opsnetmock.BasePath + "/groups/applications?cursor=", token, http.StatusBadRequest},
		{"missing_authorization", http.MethodGet, opsnetmock.BasePath + "/groups/applications", "", http.StatusUnauthorized},
		{"bearer_instead_of_networkinsight", http.MethodGet, opsnetmock.BasePath + "/groups/applications", "Bearer " + strings.TrimPrefix(token, opsnetmock.AuthPrefix), http.StatusUnauthorized},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, srv.URL()+tc.path, nil)
			if err != nil {
				t.Fatal(err)
			}
			if tc.header != "" {
				req.Header.Set("Authorization", tc.header)
			}
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			body, _ := io.ReadAll(resp.Body)
			_ = resp.Body.Close()
			if resp.StatusCode != tc.want {
				t.Errorf("%s %s = %d, want %d (body %s)", tc.method, tc.path, resp.StatusCode, tc.want, strings.TrimSpace(string(body)))
			}
		})
	}
}

func loginDirect(t *testing.T, srv *opsnetmock.Server) string {
	t.Helper()
	body := strings.NewReader(`{"username":"admin@local","password":"pw"}`)
	resp, err := http.Post(srv.URL()+opsnetmock.BasePath+"/auth/token", "application/json", body)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("login: status %d", resp.StatusCode)
	}
	var tok struct {
		Token  string `json:"token"`
		Expiry int64  `json:"expiry"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tok); err != nil {
		t.Fatal(err)
	}
	if tok.Token == "" || tok.Expiry == 0 {
		t.Fatalf("login: got %+v", tok)
	}
	return opsnetmock.AuthPrefix + tok.Token
}
