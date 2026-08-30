package vcfopslogs

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"reflect"
	"strings"
	"testing"

	contractdoc "example.com/vcfopslogs/docs"
	"example.com/vcfopslogs/internal/mockvcf"
)

const (
	pinnedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	pinnedPath   = "specifications/vcf-operations/log-management-openapi.json"
	operationID  = "getAllAgentGroupConfig"
	testToken    = "loopback-jwt-token"
)

func TestProtectedContractProvenance(t *testing.T) {
	doc, err := contractdoc.Load()
	if err != nil {
		t.Fatal(err)
	}
	if err := doc.ValidatePinnedSubset(); err != nil {
		t.Fatal(err)
	}

	checks := []struct {
		name string
		got  any
		want any
	}{
		{"openapi", doc.OpenAPI, "3.0.1"},
		{"api version", doc.Info.Version, "9.1.0.0"},
		{"source repository", doc.Source.Repository, "vmware/vcf-api-specs"},
		{"source commit", doc.Source.Commit, pinnedCommit},
		{"source path", doc.Source.Path, pinnedPath},
		{"source license", doc.Source.License, "Apache-2.0"},
		{"operation count", len(doc.Endpoints()), 1},
		{"operation id", doc.Endpoints()[0].OperationID, operationID},
		{"operation method", doc.Endpoints()[0].Method, http.MethodGet},
		{"operation path", doc.Endpoints()[0].Path, "/api/v2/agent/groups"},
	}
	for _, tc := range checks {
		t.Run(tc.name, func(t *testing.T) {
			if !reflect.DeepEqual(tc.got, tc.want) {
				t.Fatalf("got %#v, want %#v", tc.got, tc.want)
			}
		})
	}

	op := doc.Paths["/api/v2/agent/groups"].Get
	parameter := op.Parameters[0]
	if parameter.Name != "pageable" || parameter.In != "query" ||
		!parameter.Required || parameter.Style != "form" || !parameter.Explode {
		t.Fatalf("pageable query serialization drifted: %+v", parameter)
	}
	var success struct {
		Type  string `json:"type"`
		Items struct {
			Ref string `json:"$ref"`
		} `json:"items"`
	}
	if err := json.Unmarshal(op.Responses["200"].Content["application/json"].Schema, &success); err != nil {
		t.Fatal(err)
	}
	if success.Type != "array" || success.Items.Ref != "#/components/schemas/Page" {
		t.Fatalf("success envelope drifted: %+v", success)
	}
	for _, schemaName := range []string{
		"AgentGroupResponse", "Page", "Pageable", "PageableObject", "SortObject",
	} {
		if _, ok := doc.Components.Schemas[schemaName]; !ok {
			t.Errorf("contract missing schema %q", schemaName)
		}
	}

	var sources struct {
		Repository struct {
			Commit  string `json:"commit"`
			License string `json:"license"`
		} `json:"repository"`
		Specification struct {
			Path string `json:"path"`
		} `json:"specification"`
		Operations []struct {
			OperationID      string `json:"operationId"`
			Method           string `json:"method"`
			Path             string `json:"path"`
			SpecPath         string `json:"specPath"`
			RepositoryCommit string `json:"repositoryCommit"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(contractdoc.OfficialSourcesJSON(), &sources); err != nil {
		t.Fatal(err)
	}
	if sources.Repository.Commit != pinnedCommit ||
		sources.Repository.License != "Apache-2.0" ||
		sources.Specification.Path != pinnedPath ||
		len(sources.Operations) != 1 {
		t.Fatalf("official source provenance drifted: %+v", sources)
	}
	sourceOp := sources.Operations[0]
	if sourceOp.OperationID != operationID || sourceOp.Method != http.MethodGet ||
		sourceOp.Path != "/api/v2/agent/groups" ||
		sourceOp.SpecPath != pinnedPath || sourceOp.RepositoryCommit != pinnedCommit {
		t.Fatalf("operation provenance drifted: %+v", sourceOp)
	}
}

func TestListAllAgentGroupsWireAndOrdering(t *testing.T) {
	wantGroups := []AgentGroup{
		{ID: "group-alpha-1", Name: "Alpha collectors", Info: "primary", MPID: "mp-1"},
		{ID: "group-alpha-2", Name: "Alpha collectors", Info: "secondary", AutoUpdate: true},
		{ID: "group-beta", Name: "Beta collectors", AgentConfig: "beta.conf"},
		{ID: "group-delta", Name: "Delta collectors", AutoUpdate: true},
		{ID: "group-zeta", Name: "Zeta collectors", Info: "late page order", MPID: "mp-5"},
	}
	tests := []struct {
		name      string
		options   ListAgentGroupsOptions
		wantQuery []string
	}{
		{
			name:    "unset optional sort is omitted",
			options: ListAgentGroupsOptions{PageSize: 2},
			wantQuery: []string{
				"page=0&size=2",
				"page=1&size=2",
				"page=2&size=2",
			},
		},
		{
			name: "sort values repeat in caller order",
			options: ListAgentGroupsOptions{
				PageSize: 3,
				Sort:     []string{"name,asc", "id,desc"},
			},
			wantQuery: []string{
				"page=0&size=3&sort=name%2Casc&sort=id%2Cdesc",
				"page=1&size=3&sort=name%2Casc&sort=id%2Cdesc",
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			server, err := mockvcf.New(testToken)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(server.Close)
			client, err := NewClient(server.URL(), testToken, server.Client())
			if err != nil {
				t.Fatal(err)
			}
			originalSort := append([]string(nil), tc.options.Sort...)

			got, err := client.ListAllAgentGroups(context.Background(), tc.options)
			if err != nil {
				t.Fatalf("ListAllAgentGroups: %v", err)
			}
			if !reflect.DeepEqual(got, wantGroups) {
				t.Fatalf("groups:\n got: %#v\nwant: %#v", got, wantGroups)
			}
			if !reflect.DeepEqual(tc.options.Sort, originalSort) {
				t.Fatalf("caller-owned Sort mutated: got %v, want %v", tc.options.Sort, originalSort)
			}

			requests := server.Requests()
			if len(requests) != len(tc.wantQuery) {
				t.Fatalf("request count = %d, want %d: %+v", len(requests), len(tc.wantQuery), requests)
			}
			for i, req := range requests {
				if req.Method != http.MethodGet {
					t.Errorf("request %d method = %q, want GET", i, req.Method)
				}
				if req.Path != "/api/v2/agent/groups" {
					t.Errorf("request %d path = %q", i, req.Path)
				}
				if req.RawQuery != tc.wantQuery[i] {
					t.Errorf("request %d query = %q, want %q", i, req.RawQuery, tc.wantQuery[i])
				}
				wantURI := "/api/v2/agent/groups?" + tc.wantQuery[i]
				if req.RequestURI != wantURI {
					t.Errorf("request %d URI = %q, want %q", i, req.RequestURI, wantURI)
				}
				if got := req.Header.Values("X-JWT-Token"); !reflect.DeepEqual(got, []string{testToken}) {
					t.Errorf("request %d X-JWT-Token = %v", i, got)
				}
				if got := req.Header.Values("Accept"); !reflect.DeepEqual(got, []string{"application/json"}) {
					t.Errorf("request %d Accept = %v", i, got)
				}
				if got := req.Header.Values("Content-Type"); len(got) != 0 {
					t.Errorf("request %d unexpectedly sent Content-Type: %v", i, got)
				}
				if len(req.Body) != 0 {
					t.Errorf("request %d unexpectedly sent body %q", i, req.Body)
				}
				if strings.Contains(req.RawQuery, "pageable") {
					t.Errorf("request %d sent non-exploded pageable object", i)
				}
			}
		})
	}
}

func TestListOptionsRejectInvalidPageSizeWithoutRequest(t *testing.T) {
	tests := []struct {
		name string
		size int
	}{
		{"zero", 0},
		{"negative", -1},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			server, err := mockvcf.New(testToken)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(server.Close)
			client, err := NewClient(server.URL(), testToken, server.Client())
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.ListAllAgentGroups(context.Background(), ListAgentGroupsOptions{PageSize: tc.size})
			if err == nil {
				t.Fatal("expected invalid PageSize error")
			}
			if got := len(server.Requests()); got != 0 {
				t.Fatalf("invalid options made %d requests", got)
			}
		})
	}
}

func TestNewClientValidation(t *testing.T) {
	tests := []struct {
		name    string
		baseURL string
		token   string
	}{
		{"relative URL", "127.0.0.1:8787", testToken},
		{"unsupported scheme", "ftp://127.0.0.1", testToken},
		{"missing host", "http://", testToken},
		{"query on base URL", "http://127.0.0.1?route=other", testToken},
		{"fragment on base URL", "http://127.0.0.1#other", testToken},
		{"empty token", "http://127.0.0.1", ""},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := NewClient(tc.baseURL, tc.token, nil); err == nil {
				t.Fatalf("NewClient(%q, token length %d) succeeded", tc.baseURL, len(tc.token))
			}
		})
	}
}

func TestNonSuccessResponseIsError(t *testing.T) {
	server, err := mockvcf.New("different-token")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	client, err := NewClient(server.URL(), testToken, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.ListAllAgentGroups(context.Background(), ListAgentGroupsOptions{PageSize: 2})
	if err == nil || !strings.Contains(err.Error(), "403") {
		t.Fatalf("got error %v, want contextual HTTP 403 error", err)
	}
	if got := len(server.Requests()); got != 1 {
		t.Fatalf("non-success request count = %d, want 1", got)
	}
}

func TestContextCancellationIsHonored(t *testing.T) {
	server, err := mockvcf.New(testToken)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	client, err := NewClient(server.URL(), testToken, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = client.ListAllAgentGroups(ctx, ListAgentGroupsOptions{PageSize: 2})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v, want context.Canceled", err)
	}
}

func TestMockServesOnlyContractNamedOperation(t *testing.T) {
	tests := []struct {
		name   string
		method string
		path   string
		status int
	}{
		{"unnamed path", http.MethodGet, "/api/v2/agent/secrets", http.StatusNotFound},
		{"unnamed method", http.MethodPost, "/api/v2/agent/groups", http.StatusMethodNotAllowed},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			server, err := mockvcf.New(testToken)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(server.Close)
			req, err := http.NewRequest(tc.method, server.URL()+tc.path, nil)
			if err != nil {
				t.Fatal(err)
			}
			response, err := server.Client().Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer response.Body.Close()
			_, _ = io.Copy(io.Discard, response.Body)
			if response.StatusCode != tc.status {
				t.Fatalf("status = %d, want %d", response.StatusCode, tc.status)
			}
		})
	}
}
